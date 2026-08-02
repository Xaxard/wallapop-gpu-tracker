"""Central configuration. Everything tunable lives here or in the environment."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

try:  # optional: handy locally, absent on CI is fine
    from dotenv import load_dotenv

    # .env.local wins over .env (load_dotenv never overrides what's already set),
    # and both lose to real environment variables — which is what GitHub Actions
    # injects from Secrets.
    load_dotenv(".env.local")
    load_dotenv()
except ImportError:  # pragma: no cover
    pass


def _f(name: str, default: float) -> float:
    raw = os.getenv(name)
    return float(raw) if raw not in (None, "") else default


def _i(name: str, default: int) -> int:
    raw = os.getenv(name)
    return int(raw) if raw not in (None, "") else default


# --------------------------------------------------------------- secrets
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")

# --------------------------------------------------------------- geography
LATITUDE = _f("WP_LATITUDE", 40.44)          # Madrid 28020
LONGITUDE = _f("WP_LONGITUDE", -3.70)
DEFAULT_DISTANCE_KM = _i("WP_DEFAULT_DISTANCE_KM", 100)

# Wallapop's top-level "Tecnología y electrónica" tree. Kept because the
# searches table stores it, but note: verified live 2026-08-02, the API
# *silently ignores* category_ids — a request for 10304 still returns gaming
# laptops. Category filtering only works client-side, off each item's
# `taxonomy` array. See TAXONOMY_* below.
CATEGORY_GPU = "24200"

# ------------------------------------------------------------ taxonomy leaves
# Read off /api/v3/categories (verified live 2026-08-02).
TAXONOMY_COMPONENTS = 10304          # "Componentes y piezas de ordenador"

# Whole-machine categories. A GPU inside one of these is not a loose card, so
# its price is not a comp for one.
#
# Deliberately NOT used to suppress alerts: a card inside a PC is still a
# perfectly good buy, and the MAX_ALERT_PRICE cap below is what keeps whole
# machines out of the alert feed. This set exists purely to keep whole-machine
# prices out of the reference-price pool, which is the number that decides
# every ceiling.
TAXONOMY_WHOLE_MACHINE = frozenset({
    24115,   # PC gaming y streaming
    24116,   # Portátiles gaming
    24117,   # Ordenadores sobremesa gaming
    10309,   # Ordenadores de sobremesa
    10310,   # Portátiles
})


# --------------------------------------------------------------- fee model
@dataclass(frozen=True)
class FeeModel:
    """Cost structure of one flip.

    net = ref_price*(1-seller_fee) - [buy*(1+buyer_fee) + buyer_fixed + shipping_in]
    """

    seller_fee: float
    buyer_fee: float
    buyer_fixed: float
    shipping_in: float
    target_margin: float
    label: str

    def buy_ceiling(self, ref_price: float) -> float:
        """Max purchase price that still nets >= target_margin."""
        gross = ref_price * (1 - self.seller_fee)
        return (gross - self.buyer_fixed - self.shipping_in - self.target_margin) / (
            1 + self.buyer_fee
        )

    def net_margin(self, buy_price: float, ref_price: float) -> float:
        """Expected profit if bought at buy_price and resold at ref_price."""
        revenue = ref_price * (1 - self.seller_fee)
        cost = buy_price * (1 + self.buyer_fee) + self.buyer_fixed + self.shipping_in
        return revenue - cost


TARGET_MARGIN = _f("TARGET_MARGIN", 50.0)

# Default: shipped both ways — the worst case, and the gate we alert on.
# seller_fee=0 because Wallapop charges the seller nothing; only the buyer pays
# the protection fee (buyer_fee + buyer_fixed) when a shipped sale is protected.
SHIPPED = FeeModel(
    seller_fee=_f("SELLER_FEE", 0.0),
    buyer_fee=_f("BUYER_FEE", 0.075),
    buyer_fixed=_f("BUYER_FIXED", 0.69),
    shipping_in=_f("SHIPPING_IN", 4.50),
    target_margin=TARGET_MARGIN,
    label="shipped",
)

# Madrid in-person preset: no Wallapop fees, no shipping.
IN_PERSON = FeeModel(
    seller_fee=0.0,
    buyer_fee=0.0,
    buyer_fixed=0.0,
    shipping_in=0.0,
    target_margin=TARGET_MARGIN,
    label="in person",
)

# ------------------------------------------------------------- item condition
# Wallapop returns a structured condition on every listing
# (`type_attributes.condition.value`). Full enum, verified live 2026-08-02:
#
#   un_opened · in_box · new · as_good_as_new · good · fair · has_given_it_all
#
# Only the bottom tier is blocked. Everything else — including `fair` — stays:
# a card with a cosmetic flaw that still works is a legitimate flip, and the
# whole point of buying secondhand is tolerating wear the seller discounted for.
BLOCKED_CONDITIONS = frozenset(
    c.strip()
    for c in os.getenv("BLOCKED_CONDITIONS", "has_given_it_all").split(",")
    if c.strip()
)

# Sent to the API as a server-side pre-filter so blocked stock never even
# travels. The client still re-checks locally — the param is advisory and the
# search response does not echo the condition back.
ALLOWED_CONDITIONS = "un_opened,in_box,new,as_good_as_new,good,fair"

# --------------------------------------------------------------- search shape
# `order_by=newest` on its own cripples the result set — the API returns a
# heavily truncated page (measured: 13 items bare, as few as 1 once a geo and
# category filter are added) rather than a full one. Pairing it with any valid
# `time_filter` restores a full 40-item page. Measured live 2026-08-02 on the
# alert loop's exact shape, 2 pages: 34 items -> 80.
#
# Valid values are today / lastWeek / lastMonth; anything else is a 400.
ALERT_TIME_FILTER = os.getenv("ALERT_TIME_FILTER", "lastWeek")

# Deliberately empty. This is NOT symmetric with the alert loop: the comps loop
# sorts by most_relevance, which already returns full 40-item pages without a
# time filter, so adding one only narrows the pool. Measured on the comps
# loop's exact shape, 5 pages: 183 items without, 166 with lastMonth — a 9%
# loss of the distribution for no gain. Set it only if you specifically want
# to bound comps recency at the source; the 60-day window and the age decay
# already handle that far better.
COMPS_TIME_FILTER = os.getenv("COMPS_TIME_FILTER", "") or None

# --------------------------------------------------------------- comps math
MIN_COMPS = _i("MIN_COMPS", 5)
COMPS_WINDOW_DAYS = _i("COMPS_WINDOW_DAYS", 60)
TRIM_FRACTION = _f("TRIM_FRACTION", 0.10)

# Comps age out smoothly rather than falling off a 30-day cliff: a comp's
# weight halves every COMPS_HALFLIFE_DAYS. This is what lets COMPS_WINDOW_DAYS
# widen to 60 without stale prices dragging the reference down — old comps are
# still counted, just quietly.
COMPS_HALFLIFE_DAYS = _f("COMPS_HALFLIFE_DAYS", 14.0)

# A reserved listing is a real signal (someone agreed to buy at that number)
# but reservations fall through, so a confirmed sale outranks it.
RESERVED_WEIGHT = _f("RESERVED_WEIGHT", 0.7)
SOLD_WEIGHT = _f("SOLD_WEIGHT", 1.0)

# Shrinkage toward a sibling-SKU prior, so a model with 6 comps isn't trusted
# as hard as one with 60 and there's no cliff at MIN_COMPS:
#   ref = (n*observed + PRIOR_WEIGHT*prior) / (n + PRIOR_WEIGHT)
PRIOR_WEIGHT = _f("PRIOR_WEIGHT", 5.0)

# Which quantile of the comps distribution to call "the" resale price. 0.5 is
# the median — what a typical card goes for. Lowering it (0.35-0.40) prices in
# your need to sell reasonably quickly rather than eventually.
REF_PERCENTILE = _f("REF_PERCENTILE", 0.5)

# A reserved listing must vanish from this many consecutive comps runs
# before we treat it as sold.
MISSING_RUNS_FOR_SALE = _i("MISSING_RUNS_FOR_SALE", 2)

# Ignore listings not seen in this long when doing sale inference.
STALE_LISTING_DAYS = _i("STALE_LISTING_DAYS", 21)

# --------------------------------------------------------------- runtime
ALERT_MAX_PAGES = _i("ALERT_MAX_PAGES", 2)
COMPS_MAX_PAGES = _i("COMPS_MAX_PAGES", 5)
REQUEST_DELAY = _f("REQUEST_DELAY", 1.0)      # polite pause between API pages
TELEGRAM_RATE_DELAY = _f("TELEGRAM_RATE_DELAY", 0.5)
HTTP_TIMEOUT = _f("HTTP_TIMEOUT", 20.0)
HTTP_RETRIES = _i("HTTP_RETRIES", 3)
DRY_RUN = os.getenv("DRY_RUN", "0") == "1"

# Sanity band — anything outside this is a typo, a bundle, or a scam listing
# and must never enter the comps pool or trigger an alert. A real GPU under 50
# EUR is essentially always broken, fake, or a bait listing, never a genuine
# flip opportunity — so 50 is a floor, not just a typo guard.
MIN_SANE_PRICE = _f("MIN_SANE_PRICE", 50.0)
MAX_SANE_PRICE = _f("MAX_SANE_PRICE", 4000.0)

# How far below the asking price you could realistically negotiate a seller
# down. A listing qualifies if a haggled offer at this discount would clear the
# margin gate, even if the raw asking price alone would not — you can always
# present the offer and see if the seller bites.
OFFER_DISCOUNT = _f("OFFER_DISCOUNT", 0.20)

# Hard ceiling on what may ever trigger an alert, applied to the *asking*
# price before any margin maths. This is a scope decision, not a maths one:
# above it the capital at risk stops being worth it, and it's also what keeps
# whole PCs and gaming laptops out of the feed without needing to identify
# them — a machine with a card in it is essentially never listed under this.
# Comps are deliberately NOT capped: the reference price needs the full
# distribution to be meaningful.
MAX_ALERT_PRICE = _f("MAX_ALERT_PRICE", 350.0)

# ------------------------------------------------------------------- ops
# observations grows by ~one row per listing per run; at a 5-minute cadence
# that is ~100k rows/day and will exhaust a free Supabase project.
OBSERVATION_RETENTION_DAYS = _i("OBSERVATION_RETENTION_DAYS", 90)

# Consecutive runs returning zero items before the bot reports itself broken.
# The realistic failure is silent: the API changes shape, parsing yields [],
# every run "succeeds" with nothing to say, and the feed just goes quiet.
DEAD_MAN_RUNS = _i("DEAD_MAN_RUNS", 3)

# Persistent-host mode: seconds between passes when run with --loop. Wallapop's
# own search indexing lags new listings by ~150-200s (measured), so polling
# faster than this buys nothing.
LOOP_INTERVAL_SECONDS = _f("LOOP_INTERVAL_SECONDS", 45.0)


def setup_logging() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )


def require_secrets(*names: str) -> None:
    missing = [n for n in names if not globals().get(n)]
    if missing:
        raise RuntimeError(f"Missing required environment variables: {', '.join(missing)}")
