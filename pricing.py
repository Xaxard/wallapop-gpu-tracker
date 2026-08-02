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
from datetime import datetime, timedelta

import config
import models
from db import Database, now

log = logging.getLogger("pricing")


# ------------------------------------------------------------------ maths
def _trim_k(n: int, trim: float) -> int:
    """How many observations to drop from each end of a price-sorted pool.

    BUG THIS FIXES: the original `k = int(n * trim)` floors to 0 for any
    n < 10 when TRIM_FRACTION=0.10 — so with MIN_COMPS=5, every sample in
    [5, 9] items skipped trimming *entirely*. That is exactly the regime
    where a single outlier (a typo price, a bundle) is 20%+ of the sample and
    does the most damage to the median. round() plus a floor of 1 (once
    trim > 0 at all) makes trimming kick in as soon as there's enough data
    for the n-2k>=3 safety guard below to allow it, instead of only once n
    reaches double digits.
    """
    if trim <= 0 or n == 0:
        return 0
    return max(1, round(n * trim))


def trimmed_median(values: list[float], trim: float | None = None) -> float | None:
    """Median after dropping the top and bottom `trim` fraction.

    Kills the two things that wreck a secondhand median: typo prices (a 4070 at
    €30) and bundles (a whole PC listed under the GPU's name).
    """
    if not values:
        return None
    trim = config.TRIM_FRACTION if trim is None else trim
    ordered = sorted(values)
    n = len(ordered)
    k = _trim_k(n, trim)
    # Only trim when there's enough data left to be meaningful.
    if k and n - 2 * k >= 3:
        ordered = ordered[k : n - k]
    return float(statistics.median(ordered))


def _trim_pairs(pairs: list[tuple[float, float]], trim: float) -> list[tuple[float, float]]:
    """Same outlier-dropping as trimmed_median, but on (price, weight) pairs.

    Trimming is about discarding implausible *prices* (typos, bundles), so it
    still counts by number of observations, not by weight mass — a recent
    €30 typo shouldn't survive just because it would otherwise carry a high
    time-decay weight.
    """
    n = len(pairs)
    if n == 0:
        return []
    ordered = sorted(pairs, key=lambda p: p[0])
    k = _trim_k(n, trim)
    if k and n - 2 * k >= 3:
        return ordered[k : n - k]
    return ordered


def weighted_quantile(pairs: list[tuple[float, float]], q: float) -> float | None:
    """Quantile `q` of a weighted sample, via cumulative-weight interpolation.

    Each observation owns a slice of the [0, total_weight] line proportional
    to its weight; we place it at the *midpoint* of that slice (cumulative
    weight so far, minus half its own weight, normalised to [0, 1]) and
    linearly interpolate between neighbouring midpoints to find q. This is
    the standard way to make q=0.5 land on a genuine weighted median instead
    of either ignoring the weights or faking them by repeating elements
    len(weight) times (which breaks down the moment weights are fractional,
    as ours are once time-decay is applied).
    """
    if not pairs:
        return None
    ordered = sorted(pairs, key=lambda p: p[0])
    values = [v for v, _ in ordered]
    weights = [w for _, w in ordered]
    total = sum(weights)
    if total <= 0:
        return None

    positions: list[float] = []
    cum = 0.0
    for w in weights:
        cum += w
        positions.append((cum - w / 2) / total)

    if q <= positions[0]:
        return float(values[0])
    if q >= positions[-1]:
        return float(values[-1])

    for i in range(1, len(positions)):
        if positions[i] >= q:
            lo_pos, hi_pos = positions[i - 1], positions[i]
            lo_val, hi_val = values[i - 1], values[i]
            frac = (q - lo_pos) / (hi_pos - lo_pos)
            return float(lo_val + frac * (hi_val - lo_val))

    return float(values[-1])  # pragma: no cover — unreachable, positions is monotonic


def sane(price: float | None) -> bool:
    return price is not None and config.MIN_SANE_PRICE <= price <= config.MAX_SANE_PRICE


# ----------------------------------------------------------- time helpers
def _parse_dt(value: object) -> datetime | None:
    """Best-effort ISO-timestamp parse; Supabase returns 'Z'-suffixed strings."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _age_days(dt: datetime | None) -> float:
    """Age in days, floored at 0. Unknown timestamps are treated as fresh
    (weight 1.0 from the age term) rather than discarded — better to keep an
    observation at full trust than to silently drop it over a parse miss."""
    if dt is None:
        return 0.0
    return max((now() - dt).total_seconds() / 86400, 0.0)


def _time_decay(age_days: float) -> float:
    """GPU prices fall monotonically, so a 55-day-old comp is weaker evidence
    than a 2-day-old one. Halving the weight every COMPS_HALFLIFE_DAYS is what
    lets COMPS_WINDOW_DAYS widen from 30 to 60 without stale prices dragging
    the reference down — more sample, correctly discounted, rather than a hard
    cliff at the window edge.
    """
    return 0.5 ** (age_days / config.COMPS_HALFLIFE_DAYS)


# ------------------------------------------------------------- comps pool
@dataclass(frozen=True)
class Comp:
    """One priced, weighted observation feeding the reference-price pool."""

    price: float
    weight: float
    source: str  # 'sold' | 'reserved'


def collect_comps(db: Database, model_key: str) -> list[Comp]:
    """One weighted price per item: its sale price if sold, else its last
    reserved price.

    Deduping per item matters — a card sitting reserved for three weeks would
    otherwise contribute ~500 observations and single-handedly set the
    median; it must still contribute exactly once. An inferred sale outranks
    a reservation (SOLD_WEIGHT > RESERVED_WEIGHT) because reservations fall
    through, so a confirmed sale is the stronger evidence — and both are
    further discounted by age via _time_decay, so a stale comp barely moves
    the reference at all.
    """
    since = now() - timedelta(days=config.COMPS_WINDOW_DAYS)
    per_item: dict[str, Comp] = {}

    # Reserved observations, newest first, so the first hit per item wins.
    for row in db.reserved_comps(model_key, since):
        item_id = row["item_id"]
        price = row.get("price")
        if item_id in per_item or not sane(price):
            continue
        age = _age_days(_parse_dt(row.get("seen_at")))
        per_item[item_id] = Comp(float(price), config.RESERVED_WEIGHT * _time_decay(age), "reserved")

    # An inferred sale is the stronger signal, so it overwrites.
    for row in db.sold_comps(model_key, since):
        item_id = row["item_id"]
        price = row.get("sold_price")
        if not sane(price):
            continue
        age = _age_days(_parse_dt(row.get("closed_at")))
        per_item[item_id] = Comp(float(price), config.SOLD_WEIGHT * _time_decay(age), "sold")

    return list(per_item.values())


def borrowed_comps(db: Database, model_key: str) -> list[Comp]:
    """For a VRAM-less generic key, fall back to its specific SKUs' pools."""
    pool: list[Comp] = []
    for sibling in models.GENERIC_FALLBACKS.get(model_key, ()):
        pool.extend(collect_comps(db, sibling))
    return pool


def time_to_sale_days(sold_rows: list[dict]) -> float | None:
    """Median days between a listing appearing and being inferred sold.

    A 50 EUR margin realised in 6 days and one that takes 45 days to land are
    not the same trade — capital tied up for a month and a half needs a much
    fatter margin to be worth it. Returns None rather than a noisy estimate
    from a handful of closures, using the same MIN_COMPS threshold the price
    reference itself requires.

    `sold_rows` are supplied by the caller rather than fetched in here:
    Database.sold_comps() only selects item_id/sold_price/closed_at (that's
    all collect_comps needs), not the first_seen/posted_at required to
    measure elapsed time, and this module doesn't own db.py to widen that
    query. Each row needs a 'closed_at' plus either 'posted_at' (the
    seller's real listing date, preferred) or 'first_seen' (fallback: when
    we first observed it).
    """
    days: list[float] = []
    for row in sold_rows:
        closed_at = _parse_dt(row.get("closed_at"))
        started_at = _parse_dt(row.get("posted_at")) or _parse_dt(row.get("first_seen"))
        if closed_at is None or started_at is None:
            continue
        delta = (closed_at - started_at).total_seconds() / 86400
        if delta >= 0:
            days.append(delta)
    if len(days) < config.MIN_COMPS:
        return None
    return float(statistics.median(days))


def _prior_price(db: Database, model_key: str, existing: dict | None) -> float | None:
    """What to shrink the observed quantile toward.

    First choice is the model's own last learned (or seed) price — that
    field already holds "the best number we had before this run's
    evidence". Failing that (a brand-new generic key with no history of its
    own), fall back to the average learned price of its specific sibling
    SKUs via models.GENERIC_FALLBACKS — a 4060 Ti's 8GB/16GB variants are a
    far better prior than nothing.
    """
    if existing and existing.get("ref_price") is not None:
        return float(existing["ref_price"])
    siblings = models.GENERIC_FALLBACKS.get(model_key, ())
    if not siblings:
        return None
    all_prices = db.get_model_prices()
    sib_refs = [
        float(all_prices[s]["ref_price"])
        for s in siblings
        if s in all_prices and all_prices[s].get("ref_price") is not None
    ]
    return statistics.mean(sib_refs) if sib_refs else None


def recompute_model_price(
    db: Database,
    model_key: str,
    existing: dict | None,
    sold_rows: list[dict] | None = None,
) -> dict | None:
    """Recompute ref_price + ceilings for one model. Returns the row written.

    `sold_rows`, if supplied, refreshes median_days_to_sale this run (see
    time_to_sale_days for why this module can't fetch them itself). Omit it
    and the previous value is carried forward untouched.
    """
    comps = collect_comps(db, model_key)
    if len(comps) < config.MIN_COMPS:
        extra = borrowed_comps(db, model_key)
        if extra:
            log.info(
                "%s: %d own comps, borrowing %d from sibling SKUs",
                model_key, len(comps), len(extra),
            )
            comps = comps + extra

    if len(comps) < config.MIN_COMPS:
        log.info(
            "%s: only %d comps (need %d) — keeping %s",
            model_key,
            len(comps),
            config.MIN_COMPS,
            "seed" if (existing or {}).get("is_seed", True) else "previous price",
        )
        return None

    pairs = _trim_pairs([(c.price, c.weight) for c in comps], config.TRIM_FRACTION)
    raw_ref = weighted_quantile(pairs, config.REF_PERCENTILE)
    if raw_ref is None or not sane(raw_ref):
        return None

    # Shrink toward a prior instead of the old hard MIN_COMPS cliff (n=4 was
    # pure seed, n=5 was pure observed — a discontinuity). n_eff is the
    # trimmed pool's total *weight*, not a bare count, so the pull toward the
    # prior fades as real evidence accumulates — and recent sales pull harder
    # than an equal number of stale reservations.
    n_eff = sum(w for _, w in pairs)
    prior = _prior_price(db, model_key, existing)
    if prior is not None:
        ref = (n_eff * raw_ref + config.PRIOR_WEIGHT * prior) / (n_eff + config.PRIOR_WEIGHT)
        shrunk = True
    else:
        ref = raw_ref
        shrunk = False

    if not sane(ref):
        return None

    median_days = time_to_sale_days(sold_rows) if sold_rows is not None else (existing or {}).get("median_days_to_sale")

    row = {
        "model_key": model_key,
        "ref_price": round(ref, 2),
        "raw_ref": round(raw_ref, 2),
        "shrunk": shrunk,
        "n_comps": len(comps),
        "n_sold": sum(1 for c in comps if c.source == "sold"),
        "n_reserved": sum(1 for c in comps if c.source == "reserved"),
        "median_days_to_sale": median_days,
        "buy_ceiling": round(config.SHIPPED.buy_ceiling(ref), 2),
        "buy_ceiling_in_person": round(config.IN_PERSON.buy_ceiling(ref), 2),
        "updated_at": now().isoformat(),
        "is_seed": False,
    }
    db.upsert_model_price(row)
    log.info(
        "%s: ref %.2f (raw %.2f%s) from %d comps (%d sold / %d reserved) -> ceiling %.2f shipped / %.2f in person",
        model_key, row["ref_price"], row["raw_ref"], " shrunk" if shrunk else "",
        row["n_comps"], row["n_sold"], row["n_reserved"],
        row["buy_ceiling"], row["buy_ceiling_in_person"],
    )
    return row


# ------------------------------------------------------------- deal gating
@dataclass
class Deal:
    """The margin verdict for one listing.

    `offer_price` is the haggled price (asking discounted by OFFER_DISCOUNT)
    that the qualification gate actually checks — not the raw asking price.
    You can always try to negotiate down, so a listing qualifies whenever a
    realistic offer would clear the margin, even if the asking price wouldn't.
    """

    qualifies: bool
    reason: str
    ref_price: float | None = None
    ceiling_shipped: float | None = None
    ceiling_in_person: float | None = None
    offer_price: float | None = None
    net_shipped: float | None = None
    net_in_person: float | None = None
    net_shipped_at_asking: float | None = None
    is_seed: bool = False
    n_comps: int = 0
    # Provenance passthrough for the alert message — lets the owner see at a
    # glance whether a reference rests on real sales or just reservations,
    # and how long this model typically takes to move.
    median_days_to_sale: float | None = None
    n_sold: int = 0
    n_reserved: int = 0

    @property
    def priced(self) -> bool:
        return self.ref_price is not None


def evaluate(price: float, model_row: dict | None, bootstrap_cap: float | None) -> Deal:
    """Decide whether a listing clears the margin gate.

    With a reference price, the gate checks the negotiated offer price
    (asking * (1 - OFFER_DISCOUNT)) against the learned buy-ceiling — a listing
    that doesn't clear the margin at asking can still qualify if a plausible
    haggle would get there. Without a reference (too few comps, or an
    unclassifiable listing) we fall back to the search's bootstrap cap on the
    raw asking price and send a plain "matches your search" alert.
    """
    if not sane(price):
        return Deal(False, "price outside sanity band")

    if model_row and model_row.get("ref_price"):
        ref = float(model_row["ref_price"])
        ceiling = model_row.get("buy_ceiling")
        ceiling = float(ceiling) if ceiling is not None else config.SHIPPED.buy_ceiling(ref)
        ceiling_ip = model_row.get("buy_ceiling_in_person")
        ceiling_ip = float(ceiling_ip) if ceiling_ip is not None else config.IN_PERSON.buy_ceiling(ref)

        offer_price = round(price * (1 - config.OFFER_DISCOUNT), 2)
        qualifies = offer_price <= ceiling
        return Deal(
            qualifies=qualifies,
            reason="offer clears buy ceiling" if qualifies else f"offer still above ceiling {ceiling:.0f}EUR",
            ref_price=ref,
            ceiling_shipped=ceiling,
            ceiling_in_person=ceiling_ip,
            offer_price=offer_price,
            net_shipped=config.SHIPPED.net_margin(offer_price, ref),
            net_in_person=config.IN_PERSON.net_margin(offer_price, ref),
            net_shipped_at_asking=config.SHIPPED.net_margin(price, ref),
            is_seed=bool(model_row.get("is_seed")),
            n_comps=int(model_row.get("n_comps") or 0),
            median_days_to_sale=(
                float(model_row["median_days_to_sale"])
                if model_row.get("median_days_to_sale") is not None
                else None
            ),
            n_sold=int(model_row.get("n_sold") or 0),
            n_reserved=int(model_row.get("n_reserved") or 0),
        )

    if bootstrap_cap is not None:
        under = price <= float(bootstrap_cap)
        return Deal(
            under,
            "under bootstrap cap" if under else f"above bootstrap cap {float(bootstrap_cap):.0f}EUR",
        )

    return Deal(False, "no reference price and no bootstrap cap")
