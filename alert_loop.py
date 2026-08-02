"""Fast loop (every 5 min): find qualifying listings and push Telegram alerts.

Entry point is `run_once()` so the same code runs under GitHub Actions cron
today and inside a `while True` loop on a VM later without changes.
"""

from __future__ import annotations

import logging
import statistics
import sys
import time
import traceback

import config
import junk
import models
import pricing
from alerts import Telegram
from db import Database, iso, now
from wallapop_client import Item, WallapopClient

log = logging.getLogger("alert")


def _listing_row(item: Item, match: models.Match) -> dict:
    return {
        "item_id": item.item_id,
        "title": item.title[:500],
        "description": (item.description or "")[:2000],
        "model_key": match.model_key,
        "confidence": match.confidence if match.model_key else None,
        "last_seen": iso(now()),
        "last_price": item.price,
        "last_status": item.status,
        "web_url": item.web_url,
        "image_url": item.image_url,
        "shipping": item.shipping,
        "location": str(item.location) if item.location else None,
        "distance_km": item.distance_km,
        "country": item.country,
        "missing_runs": 0,
        # Structured fields the API hands over that were previously re-derived
        # from free text, or ignored entirely.
        "condition": item.condition,
        "brand": item.brand,
        "taxonomy": list(item.taxonomy) or None,
        "whole_machine": item.whole_machine,
        "posted_at": iso(item.posted_at) if item.posted_at else None,
        "user_allows_shipping": item.user_allows_shipping,
    }


def _relevant(search: dict, match: models.Match) -> bool:
    """Guard against Wallapop's loose keyword matching.

    `order_by=newest` returns anything vaguely related — a search for "rtx 3070"
    comes back with Ryzen CPUs and wifi cards. Under a bootstrap cap those would
    all "qualify", so a model-targeted search only accepts listings that
    actually classify to that model.
    """
    model_key = search.get("model_key")
    if model_key:
        return models.same_family(model_key, match.model_key)
    if search.get("category_ids") == config.CATEGORY_GPU:
        # Broad discovery searches: must at least be an identifiable card.
        return match.model_key is not None
    # Non-GPU keyword watches (google pixel) stay plain keyword matches.
    return True


def _enrich(wp: WallapopClient, item: Item) -> Item:
    """Fill in the fields only the detail endpoint returns.

    Worth one extra request *only* for listings that already cleared the
    margin gate — a handful per run, not the ~400 the search returns. The
    search payload carries no condition at all, and condition is the single
    strongest predictor of a dead card: every false alert measured before this
    change was a listing the API itself labelled `has_given_it_all` or `fair`.
    """
    try:
        detail = wp.fetch_detail(item.item_id)
    except Exception:
        log.warning("detail fetch failed for %s — proceeding on search data", item.item_id)
        return item
    if detail is None:
        return item
    for field in ("condition", "brand", "user_allows_shipping"):
        value = getattr(detail, field, None)
        if value is not None:
            setattr(item, field, value)
    if getattr(detail, "taxonomy", ()):
        item.taxonomy = detail.taxonomy
    return item


def _alert_latency(item: Item) -> float | None:
    """Seconds from the seller pressing publish to us deciding to alert.

    The number that actually matters, and the one nothing was measuring.
    Wallapop's own search indexing accounts for ~150-200s of it before this
    process ever gets a chance to see the listing.
    """
    age = getattr(item, "age_seconds", None)
    return round(age, 1) if age is not None else None


def _decide_kind(price: float, already_sent: list[float]) -> str | None:
    """'new', 'price_drop', or None when this listing+price was already sent."""
    if not already_sent:
        return "new"
    if price in already_sent:
        return None
    return "price_drop" if price < min(already_sent) else None


def run_once() -> dict:
    """One full pass over the alert searches. Returns a small stats dict."""
    config.setup_logging()
    config.require_secrets("TELEGRAM_TOKEN", "TELEGRAM_CHAT_ID")

    db = Database()
    run_id = db.start_run("alert")
    stats: dict = {
        "items_seen": 0,
        "alerts_sent": 0,
        "junk": 0,
        "errors": 0,
        "over_cap": 0,
        "blocked_condition": 0,
        "latency_samples": [],
    }
    telegram = Telegram()
    detail_client: WallapopClient | None = None

    try:
        searches = db.get_searches("alert")
        if not searches:
            log.warning("No active alert searches configured — run seed.py first")
            return stats

        model_prices = db.get_model_prices()

        # item_id -> (item, match, originating search)
        found: dict[str, tuple[Item, models.Match, dict]] = {}
        junk_rows: list[dict] = []

        with WallapopClient() as wp:
            for search in searches:
                # Nationwide/international — Wallapop is one shared marketplace
                # across Spain, Portugal, Italy etc., and there's no API-side
                # price cap here either: the search's max_price is only the
                # bootstrap fallback used below when a model has no learned
                # reference price yet. The real filter is the margin gate.
                items = wp.search(
                    search["keywords"],
                    min_price=search.get("min_price"),
                    category_ids=search.get("category_ids"),
                    order_by="newest",
                    max_pages=config.ALERT_MAX_PAGES,
                    nationwide=True,
                    # 2.5x the items for the same request count, and the
                    # server drops the bottom condition tier before it ever
                    # travels. Both verified live 2026-08-02.
                    time_filter=config.ALERT_TIME_FILTER,
                    condition=config.ALLOWED_CONDITIONS,
                )
                count = kept = 0
                for item in items:
                    count += 1
                    if item.item_id in found:
                        continue
                    verdict = junk.check(item.title, item.description)
                    if verdict.excluded:
                        junk_rows.append(
                            {
                                "item_id": item.item_id,
                                "title": item.title[:300],
                                "phrase": verdict.phrase,
                                "category": verdict.category,
                            }
                        )
                        continue
                    match = models.classify(item.title, item.description)
                    if not _relevant(search, match):
                        continue
                    kept += 1
                    found[item.item_id] = (item, match, search)
                log.info("%-14s %3d returned, %d relevant", search["label"], count, kept)

        stats["items_seen"] = len(found)
        stats["junk"] = len(junk_rows)
        db.log_junk(junk_rows)

        if not found:
            log.info("Nothing to evaluate")
            return stats

        # Record everything we saw, reserved included — the alert loop feeds the
        # comps pool too, and its 5-minute cadence catches short-lived listings
        # the hourly comps loop would miss entirely.
        db.upsert_listings([_listing_row(i, m) for i, m, _ in found.values()])
        # A whole machine's price is recorded but never attributed to a model:
        # a prebuilt that sells for 900 EUR is a real transaction, just not a
        # transaction in the loose card its title happens to name. Nulling the
        # model_key keeps it out of every comps pool at the source, which is
        # the only number that decides a ceiling.
        db.insert_observations(
            [
                {
                    "item_id": item.item_id,
                    "model_key": (
                        match.model_key
                        if match.priceable and not item.whole_machine
                        else None
                    ),
                    "price": item.price,
                    "status": item.status,
                    "seen_at": iso(now()),
                }
                for item, match, _ in found.values()
                if item.price is not None
            ]
        )

        # Reserved listings price the market but you can't buy them.
        #
        # MAX_ALERT_PRICE is a scope decision applied to the raw asking price
        # before any margin maths: above it the capital at risk stops being
        # worth it. It also removes the need to identify whole PCs and gaming
        # laptops in the alert path at all — a machine with a card in it is
        # essentially never listed this cheap, so the cap filters them out on
        # price without ever having to guess at form factor from a title.
        candidates = {
            iid: v
            for iid, v in found.items()
            if not v[0].reserved
            and v[0].price is not None
            and float(v[0].price) <= config.MAX_ALERT_PRICE
        }
        over_cap = sum(
            1
            for _, v in found.items()
            if v[0].price is not None and float(v[0].price) > config.MAX_ALERT_PRICE
        )
        stats["over_cap"] = over_cap
        log.info(
            "%d candidates under %.0f EUR (%d listings priced above the cap)",
            len(candidates), config.MAX_ALERT_PRICE, over_cap,
        )
        already = db.alerted_prices(list(candidates))
        detail_client = WallapopClient()

        for item, match, search in candidates.values():
            kind = _decide_kind(float(item.price), already.get(item.item_id, []))
            if kind is None:
                continue

            model_row = (
                model_prices.get(match.model_key)
                if match.priceable and match.model_key
                else None
            )
            deal = pricing.evaluate(
                float(item.price), model_row, search.get("max_price")
            )
            if not deal.qualifies:
                log.debug("skip %s (%s): %s", item.item_id, item.title[:40], deal.reason)
                continue

            item = _enrich(detail_client, item)

            # Only the bottom tier is blocked. `fair` deliberately stays: a
            # cosmetic flaw on a card that still works is exactly the discount
            # a flip is built on.
            if item.condition in config.BLOCKED_CONDITIONS:
                stats["blocked_condition"] += 1
                log.info(
                    "skip %s — condition %s: %s",
                    item.item_id, item.condition, item.title[:50],
                )
                continue

            previous = min(already.get(item.item_id, [float(item.price)]))
            try:
                sent = telegram.send_alert(
                    item,
                    deal,
                    kind,
                    previous_price=previous if kind == "price_drop" else None,
                    model_display=match.display,
                )
            except Exception:
                stats["errors"] += 1
                log.exception("send failed for %s", item.item_id)
                continue

            if sent:
                stats["alerts_sent"] += 1
                latency = _alert_latency(item)
                if latency is not None:
                    stats["latency_samples"].append(latency)
                log.info(
                    "ALERT %-12s %s %.0f EUR (%s old) — %s",
                    kind,
                    match.model_key or "unclassified",
                    item.price,
                    f"{latency / 60:.1f}min" if latency is not None else "age unknown",
                    item.title[:60],
                )
                # DRY_RUN never touches Telegram, so it must never touch the
                # dedup table either — a dry-run "send" marking an item as
                # already-alerted would silently suppress the real alert the
                # next time this actually runs for real.
                if not config.DRY_RUN:
                    db.record_alert(item.item_id, float(item.price), kind)

        return stats

    except Exception:
        stats["errors"] += 1
        trace = traceback.format_exc()
        log.error("alert loop crashed:\n%s", trace)
        try:
            telegram.send_error(f"alert_loop crashed\n{trace[-1200:]}")
        except Exception:
            log.exception("could not deliver error ping")
        raise
    finally:
        samples = stats["latency_samples"]
        notes = (
            f"junk_filtered={stats['junk']} over_cap={stats['over_cap']} "
            f"blocked_condition={stats['blocked_condition']}"
        )
        if samples:
            notes += f" median_latency_s={statistics.median(samples):.0f}"
        db.finish_run(
            run_id,
            items_seen=stats["items_seen"],
            alerts_sent=stats["alerts_sent"],
            errors=stats["errors"],
            notes=notes,
        )
        # Runs after finish_run so this run's own row counts toward the streak.
        try:
            _check_dead_man(db, telegram)
        except Exception:
            log.warning("dead-man check failed", exc_info=True)
        if detail_client is not None:
            detail_client.close()
        telegram.close()


def _check_dead_man(db: Database, telegram: Telegram) -> None:
    """Warn when the bot has gone quiet rather than actually broken.

    The realistic failure here is silent, not loud: Wallapop changes the
    response shape, parsing yields an empty list, every run still "succeeds"
    with nothing to report, and the feed simply stops. Without this the first
    signal is noticing weeks later that no alert ever arrived.
    """
    runs = db.recent_runs("alert", config.DEAD_MAN_RUNS)
    if len(runs) < config.DEAD_MAN_RUNS:
        return
    if all(int(r.get("items_seen") or 0) == 0 for r in runs):
        log.error("dead-man switch: %d consecutive runs saw zero items", len(runs))
        telegram.send_error(
            f"{config.DEAD_MAN_RUNS} consecutive alert runs returned zero listings.\n"
            "The API response shape has probably changed — run smoke_test.py."
        )


def main() -> None:
    """One pass, or a self-paced loop on a persistent host.

    `--loop` exists because the poll gap is the only part of the latency
    budget still worth attacking: Wallapop's indexing costs ~150-200s no
    matter what, so a 45s cadence lands near the floor while a 5-minute one
    adds ~2.5 minutes of pure waiting. Under GitHub Actions the default
    single-pass mode is still correct.
    """
    once = "--loop" not in sys.argv
    while True:
        result = run_once()
        print(
            f"items={result['items_seen']} alerts={result['alerts_sent']} "
            f"junk={result['junk']} over_cap={result['over_cap']} "
            f"errors={result['errors']}"
        )
        if once:
            return
        time.sleep(config.LOOP_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
