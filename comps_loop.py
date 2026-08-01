"""Slow loop (hourly): learn what each GPU is actually worth.

Runs the uncapped, nationwide comps searches, records active + reserved state,
infers sales from reserved listings that disappear, and rewrites each model's
reference price and buy-ceiling.
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
    }


def infer_sales(db: Database, covered_models: set[str], seen_ids: set[str]) -> int:
    """Reserved listing that vanished from N consecutive runs == sold.

    Wallapop exposes no sold feed, so this is the proxy. Items that disappear
    without ever being reserved are closed as *uncertain* and never enter the
    comps pool — a listing pulled because the seller gave up tells us nothing
    about the market price.
    """
    open_listings = db.get_open_listings_for_models(sorted(covered_models))
    closed = 0

    for row in open_listings:
        item_id = row["item_id"]
        if item_id in seen_ids:
            if row.get("missing_runs"):
                db.set_missing_runs(item_id, 0)
            continue

        misses = int(row.get("missing_runs") or 0) + 1
        if misses < config.MISSING_RUNS_FOR_SALE:
            db.set_missing_runs(item_id, misses)
            continue

        was_reserved = bool(row.get("ever_reserved")) or row.get("last_status") == "reserved"
        if was_reserved:
            price = db.last_reserved_price(item_id) or row.get("last_price")
            db.mark_closed(item_id, float(price) if price is not None else None)
            log.info(
                "SOLD %s @ %s — %s",
                item_id, price, (row.get("title") or "")[:50],
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
    stats = {"items_seen": 0, "closed": 0, "models_updated": 0, "errors": 0}
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
        if reserved_rows:
            db.upsert_listings(reserved_rows)
        if active_rows:
            db.upsert_listings(active_rows)

        db.insert_observations(
            [
                {
                    "item_id": item.item_id,
                    "model_key": match.model_key if match.priceable else None,
                    "price": item.price,
                    "status": item.status,
                    "seen_at": iso(now()),
                }
                for item, match in found.values()
                if item.price is not None
            ]
        )

        # Only models whose searches actually ran this cycle may be judged
        # absent — otherwise a model with no comps search would have all its
        # listings "sold" on the first run.
        covered = {s["model_key"] for s in searches if s.get("model_key")}
        if covered:
            stats["closed"] = infer_sales(db, covered, set(found))

        existing = db.get_model_prices()
        targets = covered | {
            m.model_key for _, m in found.values() if m.priceable and m.model_key
        }
        for model_key in sorted(targets):
            try:
                if pricing.recompute_model_price(db, model_key, existing.get(model_key)):
                    stats["models_updated"] += 1
            except Exception:
                stats["errors"] += 1
                log.exception("recompute failed for %s", model_key)

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
