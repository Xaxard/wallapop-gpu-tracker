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

# GPU / components category on Wallapop
CATEGORY_GPU = "24200"


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

# --------------------------------------------------------------- comps math
MIN_COMPS = _i("MIN_COMPS", 5)
COMPS_WINDOW_DAYS = _i("COMPS_WINDOW_DAYS", 30)
TRIM_FRACTION = _f("TRIM_FRACTION", 0.10)

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
