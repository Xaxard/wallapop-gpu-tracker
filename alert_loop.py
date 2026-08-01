"""Fast loop (every 5 min): find qualifying listings and push Telegram alerts.

Entry point is `run_once()` so the same code runs under GitHub Actions cron
today and inside a `while True` loop on a VM later without changes.
"""

from __future__ import annotations

import logging
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
        "missing_runs": 0,
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
    stats = {"items_seen": 0, "alerts_sent": 0, "junk": 0, "errors": 0}
    telegram = Telegram()

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
                # The API-side cap is always clamped to the hard budget ceiling,
                # which keeps the response volume down. The search's own
                # max_price still governs the bootstrap gate below.
                cap = search.get("max_price")
                api_cap = min(float(cap), config.MAX_DEAL_PRICE) if cap else config.MAX_DEAL_PRICE

                items = wp.search(
                    search["keywords"],
                    min_price=search.get("min_price"),
                    max_price=api_cap,
                    category_ids=search.get("category_ids"),
                    distance_km=search.get("distance_km"),
                    order_by="newest",
                    max_pages=config.ALERT_MAX_PAGES,
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
        db.insert_observations(
            [
                {
                    "item_id": item.item_id,
                    "model_key": match.model_key if match.priceable else None,
                    "price": item.price,
                    "status": item.status,
                    "seen_at": iso(now()),
                }
                for item, match, _ in found.values()
                if item.price is not None
            ]
        )

        # Reserved listings price the market but you can't buy them.
        candidates = {
            iid: v for iid, v in found.items() if not v[0].reserved and v[0].price is not None
        }
        already = db.alerted_prices(list(candidates))

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
                db.record_alert(item.item_id, float(item.price), kind)
                stats["alerts_sent"] += 1
                log.info(
                    "ALERT %-12s %s %.0f EUR — %s",
                    kind, match.model_key or "unclassified", item.price, item.title[:60],
                )

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
        db.finish_run(
            run_id,
            items_seen=stats["items_seen"],
            alerts_sent=stats["alerts_sent"],
            errors=stats["errors"],
            notes=f"junk_filtered={stats['junk']}",
        )
        telegram.close()


if __name__ == "__main__":
    result = run_once()
    print(
        f"items={result['items_seen']} alerts={result['alerts_sent']} "
        f"junk={result['junk']} errors={result['errors']}"
    )
