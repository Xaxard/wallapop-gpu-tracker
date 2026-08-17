"""Slow loop (hourly): learn what each GPU is actually worth.

Runs the uncapped, nationwide comps searches, records active + reserved state,
infers sales from reserved listings that disappear, and rewrites each model's
reference price and buy-ceiling.
"""

from __future__ import annotations

import logging
import traceback
from datetime import timedelta

import config
import junk
import models
import pricing
from alerts import Telegram
from db import Database, iso, now
from wallapop_client import Item, WallapopClient

log = logging.getLogger("comps")


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
        "condition": item.condition,
        "brand": item.brand,
        "taxonomy": list(item.taxonomy) or None,
        "whole_machine": item.whole_machine,
        "posted_at": iso(item.posted_at) if item.posted_at else None,
        "user_allows_shipping": item.user_allows_shipping,
    }


def infer_sales(
    db: Database,
    covered_models: set[str],
    seen_ids: set[str],
    wp: WallapopClient | None = None,
) -> int:
    """Decide which listings have closed, and at what price.

    Wallapop exposes no sold feed, so absence has always been the proxy. But
    absence from *our search results* is a weak signal — a listing that merely
    slid down the ranking looks identical to one that sold. The detail endpoint
    settles it directly: it 404s once a listing is gone, so `wp` upgrades the
    heuristic into a fact.

    That cuts both ways, and both directions matter:
      * confirmed gone -> close it now, instead of waiting out
        MISSING_RUNS_FOR_SALE more hourly runs (~2h of stale ceilings);
      * confirmed alive -> reset the counter, which stops a listing that simply
        fell out of the rankings from ever being booked as a phantom sale.

    A failed request returns None and falls back to the counter — a network
    blip must never be read as a sale.
    """
    open_listings = db.get_open_listings_for_models(sorted(covered_models))
    closed = 0

    for row in open_listings:
        item_id = row["item_id"]
        if item_id in seen_ids:
            if row.get("missing_runs"):
                db.set_missing_runs(item_id, 0)
            continue

        alive: bool | None = None
        if wp is not None:
            try:
                alive = wp.is_alive(item_id)
            except Exception:
                alive = None

        if alive is True:
            if row.get("missing_runs"):
                db.set_missing_runs(item_id, 0)
            continue

        if alive is None:
            misses = int(row.get("missing_runs") or 0) + 1
            if misses < config.MISSING_RUNS_FOR_SALE:
                db.set_missing_runs(item_id, misses)
                continue

        was_reserved = bool(row.get("ever_reserved")) or row.get("last_status") == "reserved"
        if was_reserved:
            price = db.last_reserved_price(item_id) or row.get("last_price")
            db.mark_closed(item_id, float(price) if price is not None else None)
            log.info(
                "SOLD %s @ %s (%s) — %s",
                item_id,
                price,
                "confirmed gone" if alive is False else "absent from runs",
                (row.get("title") or "")[:50],
            )
        else:
            db.mark_closed(item_id, None)  # closed, uncertain — excluded from comps
        closed += 1

    return closed


def run_once() -> dict:
    """One full comps pass. Returns a small stats dict."""
    config.setup_logging()

    db = Database()
    run_id = db.start_run("comps")
    stats = {"items_seen": 0, "closed": 0, "models_updated": 0, "errors": 0, "observations": 0}
    telegram = Telegram()

    try:
        searches = db.get_searches("comps")
        if not searches:
            log.warning("No active comps searches configured — run seed.py first")
            return stats

        found: dict[str, tuple[Item, models.Match]] = {}
        junk_rows: list[dict] = []

        with WallapopClient() as wp:
            for search in searches:
                items = wp.search(
                    search["keywords"],
                    min_price=search.get("min_price"),
                    max_price=search.get("max_price"),  # null for comps
                    category_ids=search.get("category_ids"),
                    order_by="most_relevance",
                    max_pages=config.COMPS_MAX_PAGES,
                    nationwide=True,
                    # Normally None — see config. most_relevance already
                    # returns full pages, so a time filter here would only
                    # shrink the distribution, and depth is the whole point of
                    # this loop.
                    time_filter=config.COMPS_TIME_FILTER,
                )
                count = 0
                for item in items:
                    count += 1
                    if item.item_id in found:
                        continue
                    verdict = junk.check(item.title, item.description)
                    if verdict.excluded:
                        # A broken card's price would drag every median down.
                        junk_rows.append(
                            {
                                "item_id": item.item_id,
                                "title": item.title[:300],
                                "phrase": verdict.phrase,
                                "category": verdict.category,
                            }
                        )
                        continue
                    found[item.item_id] = (item, models.classify(item.title, item.description))
                log.info("%-20s %3d items", search["label"], count)

        stats["items_seen"] = len(found)
        db.log_junk(junk_rows)

        # ever_reserved is sticky, so reserved and non-reserved rows are written
        # as separate batches: the non-reserved batch simply omits the column
        # rather than resetting it to false.
        reserved_rows, active_rows = [], []
        for item, match in found.values():
            row = _listing_row(item, match)
            if item.reserved:
                row["ever_reserved"] = True
                reserved_rows.append(row)
            else:
                active_rows.append(row)

        # Observations compare against the previous listing state, so they are
        # written before the upserts below replace it.
        #
        # A whole machine never contributes a model_key, so its price can never
        # reach a comps pool. This is the one place form factor genuinely
        # matters: a prebuilt selling for 900 EUR is a real transaction, just
        # not one in the loose card its title names, and the reference price is
        # the number every buy ceiling is derived from.
        stats["observations"] = db.insert_changed_observations(
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
                for item, match in found.values()
                if item.price is not None
            ]
        )

        if reserved_rows:
            db.upsert_listings(reserved_rows)
        if active_rows:
            db.upsert_listings(active_rows)

        # Only models whose searches actually ran this cycle may be judged
        # absent — otherwise a model with no comps search would have all its
        # listings "sold" on the first run. A generic search (e.g. "rtx 4060
        # ti") classifies some listings into its split-VRAM children
        # (rtx_4060_ti_8g/16g), which have no search row of their own, so those
        # children are covered too — otherwise their listings would never be
        # checked for closure and would never contribute a sold comp.
        covered = {s["model_key"] for s in searches if s.get("model_key")}
        for key in list(covered):
            covered.update(models.GENERIC_FALLBACKS.get(key, ()))
        if covered:
            with WallapopClient() as liveness:
                stats["closed"] = infer_sales(db, covered, set(found), liveness)

        existing = db.get_model_prices()
        targets = covered | {
            m.model_key for _, m in found.values() if m.priceable and m.model_key
        }
        since = now() - timedelta(days=config.COMPS_WINDOW_DAYS)
        for model_key in sorted(targets):
            try:
                # sold_rows carries closed_at plus posted_at/first_seen, which
                # is what turns "50 EUR of margin" into "50 EUR of margin in
                # 6 days" — the pricing module can't fetch it itself.
                if pricing.recompute_model_price(
                    db,
                    model_key,
                    existing.get(model_key),
                    sold_rows=db.sold_durations(model_key, since),
                ):
                    stats["models_updated"] += 1
            except Exception:
                stats["errors"] += 1
                log.exception("recompute failed for %s", model_key)

        # Housekeeping runs on the slow loop so the alert path stays lean.
        db.purge_old_observations()
        db.purge_old_junk()

        log.info(
            "comps done: %d items, %d closed, %d models repriced",
            stats["items_seen"], stats["closed"], stats["models_updated"],
        )
        return stats

    except Exception:
        stats["errors"] += 1
        trace = traceback.format_exc()
        log.error("comps loop crashed:\n%s", trace)
        try:
            telegram.send_error(f"comps_loop crashed\n{trace[-1200:]}")
        except Exception:
            log.exception("could not deliver error ping")
        raise
    finally:
        db.finish_run(
            run_id,
            items_seen=stats["items_seen"],
            errors=stats["errors"],
            notes=f"closed={stats['closed']} repriced={stats['models_updated']}",
        )
        telegram.close()


if __name__ == "__main__":
    result = run_once()
    print(
        f"items={result['items_seen']} closed={result['closed']} "
        f"repriced={result['models_updated']} errors={result['errors']}"
    )
