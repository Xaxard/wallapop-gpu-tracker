"""Comps aggregation and the flip-margin engine.

The reference price is deliberately *not* the mean of active asking prices —
those are aspirational. It's the trimmed median of what the market actually
transacted at: reserved listings (someone agreed to buy) plus listings inferred
sold (reserved, then vanished).
"""

from __future__ import annotations

import logging
import statistics
from dataclasses import dataclass
from datetime import timedelta

import config
import models
from db import Database, now

log = logging.getLogger("pricing")


# ------------------------------------------------------------------ maths
def trimmed_median(values: list[float], trim: float | None = None) -> float | None:
    """Median after dropping the top and bottom `trim` fraction.

    Kills the two things that wreck a secondhand median: typo prices (a 4070 at
    €30) and bundles (a whole PC listed under the GPU's name).
    """
    if not values:
        return None
    trim = config.TRIM_FRACTION if trim is None else trim
    ordered = sorted(values)
    k = int(len(ordered) * trim)
    # Only trim when there's enough data left to be meaningful.
    if k and len(ordered) - 2 * k >= 3:
        ordered = ordered[k : len(ordered) - k]
    return float(statistics.median(ordered))


def sane(price: float | None) -> bool:
    return price is not None and config.MIN_SANE_PRICE <= price <= config.MAX_SANE_PRICE


# ------------------------------------------------------------- comps pool
def collect_comps(db: Database, model_key: str) -> list[float]:
    """One price per item: its sale price if sold, else its last reserved price.

    Deduping per item matters — a card sitting reserved for three weeks would
    otherwise contribute ~500 observations and single-handedly set the median.
    """
    since = now() - timedelta(days=config.COMPS_WINDOW_DAYS)
    per_item: dict[str, float] = {}

    # Reserved observations, newest first, so the first hit per item wins.
    for row in db.reserved_comps(model_key, since):
        price = row.get("price")
        if row["item_id"] not in per_item and sane(price):
            per_item[row["item_id"]] = float(price)

    # An inferred sale is the stronger signal, so it overwrites.
    for row in db.sold_comps(model_key, since):
        price = row.get("sold_price")
        if sane(price):
            per_item[row["item_id"]] = float(price)

    return list(per_item.values())


def borrowed_comps(db: Database, model_key: str) -> list[float]:
    """For a VRAM-less generic key, fall back to its specific SKUs' pools."""
    pools: list[float] = []
    for sibling in models.GENERIC_FALLBACKS.get(model_key, ()):
        pools.extend(collect_comps(db, sibling))
    return pools


def recompute_model_price(db: Database, model_key: str, existing: dict | None) -> dict | None:
    """Recompute ref_price + ceilings for one model. Returns the row written."""
    prices = collect_comps(db, model_key)
    if len(prices) < config.MIN_COMPS:
        extra = borrowed_comps(db, model_key)
        if extra:
            log.info(
                "%s: %d own comps, borrowing %d from sibling SKUs",
                model_key, len(prices), len(extra),
            )
            prices = prices + extra

    if len(prices) < config.MIN_COMPS:
        log.info(
            "%s: only %d comps (need %d) — keeping %s",
            model_key,
            len(prices),
            config.MIN_COMPS,
            "seed" if (existing or {}).get("is_seed", True) else "previous price",
        )
        return None

    ref = trimmed_median(prices)
    if ref is None or not sane(ref):
        return None

    row = {
        "model_key": model_key,
        "ref_price": round(ref, 2),
        "n_comps": len(prices),
        "buy_ceiling": round(config.SHIPPED.buy_ceiling(ref), 2),
        "buy_ceiling_in_person": round(config.IN_PERSON.buy_ceiling(ref), 2),
        "updated_at": now().isoformat(),
        "is_seed": False,
    }
    db.upsert_model_price(row)
    log.info(
        "%s: ref %.2f from %d comps -> ceiling %.2f shipped / %.2f in person",
        model_key, row["ref_price"], row["n_comps"],
        row["buy_ceiling"], row["buy_ceiling_in_person"],
    )
    return row


# ------------------------------------------------------------- deal gating
@dataclass
class Deal:
    """The margin verdict for one listing."""

    qualifies: bool
    reason: str
    ref_price: float | None = None
    ceiling_shipped: float | None = None
    ceiling_in_person: float | None = None
    net_shipped: float | None = None
    net_in_person: float | None = None
    is_seed: bool = False
    n_comps: int = 0

    @property
    def priced(self) -> bool:
        return self.ref_price is not None


def evaluate(price: float, model_row: dict | None, bootstrap_cap: float | None) -> Deal:
    """Decide whether a listing clears the margin gate.

    With a reference price we use the learned buy-ceiling. Without one (a model
    with too few comps, or an unclassifiable listing) we fall back to the
    search's bootstrap cap and send a plain "matches your search" alert.
    """
    if not sane(price):
        return Deal(False, "price outside sanity band")

    # Hard budget ceiling, applied before anything else: a 4090 at 700 EUR may
    # be a superb margin, but it's not a deal you want to be shown.
    if price > config.MAX_DEAL_PRICE:
        return Deal(False, f"above hard budget ceiling {config.MAX_DEAL_PRICE:.0f}EUR")

    if model_row and model_row.get("ref_price"):
        ref = float(model_row["ref_price"])
        ceiling = model_row.get("buy_ceiling")
        ceiling = float(ceiling) if ceiling is not None else config.SHIPPED.buy_ceiling(ref)
        ceiling_ip = model_row.get("buy_ceiling_in_person")
        ceiling_ip = float(ceiling_ip) if ceiling_ip is not None else config.IN_PERSON.buy_ceiling(ref)
        deal = Deal(
            qualifies=price <= ceiling,
            reason="clears buy ceiling" if price <= ceiling else f"above ceiling {ceiling:.0f}EUR",
            ref_price=ref,
            ceiling_shipped=ceiling,
            ceiling_in_person=ceiling_ip,
            net_shipped=config.SHIPPED.net_margin(price, ref),
            net_in_person=config.IN_PERSON.net_margin(price, ref),
            is_seed=bool(model_row.get("is_seed")),
            n_comps=int(model_row.get("n_comps") or 0),
        )
        return deal

    if bootstrap_cap is not None:
        under = price <= float(bootstrap_cap)
        return Deal(
            under,
            "under bootstrap cap" if under else f"above bootstrap cap {float(bootstrap_cap):.0f}EUR",
        )

    return Deal(False, "no reference price and no bootstrap cap")
