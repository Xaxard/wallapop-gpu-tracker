"""End-to-end wiring for both loops, with the DB, API and Telegram stubbed.

These catch the integration bugs unit tests miss — a dedup rule that never
fires, a reserved listing that gets alerted, a sale inferred one run too early.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import alert_loop  # noqa: E402
import comps_loop  # noqa: E402
import config  # noqa: E402
from wallapop_client import Item  # noqa: E402


def make_item(
    item_id,
    title,
    price,
    reserved=False,
    description="",
    condition=None,
    taxonomy=(),
):
    return Item(
        item_id=item_id,
        title=title,
        description=description,
        price=price,
        currency="EUR",
        web_url=f"https://es.wallapop.com/item/{item_id}",
        image_url="https://cdn/x.jpg",
        reserved=reserved,
        shipping=True,
        location="Madrid",
        distance_km=4.0,
        condition=condition,
        taxonomy=taxonomy,
    )


class FakeDB:
    def __init__(
        self,
        searches,
        model_prices=None,
        alerted=None,
        open_listings=None,
        runs=None,
    ):
        self._searches = searches
        self._model_prices = model_prices or {}
        self._alerted = alerted or {}
        self._open = open_listings or []
        self._runs = runs or []
        self.purged = False
        self.listings = []
        self.observations = []
        self.recorded = []
        self.junk = []
        self.closed = {}
        self.missing_set = {}
        self.model_writes = []

    # --- alert loop surface
    def start_run(self, name):
        return 1

    def finish_run(self, run_id, **fields):
        pass

    def get_searches(self, role):
        return [s for s in self._searches if s["role"] == role]

    def get_model_prices(self):
        return self._model_prices

    def log_junk(self, rows):
        self.junk.extend(rows)

    def upsert_listings(self, rows):
        self.listings.extend(rows)

    def insert_observations(self, rows):
        self.observations.extend(rows)

    def alerted_prices(self, ids):
        return {k: v for k, v in self._alerted.items() if k in set(ids)}

    def record_alert(self, item_id, price, kind):
        self.recorded.append((item_id, price, kind))

    # --- comps loop surface
    def get_open_listings_for_models(self, keys):
        return [r for r in self._open if r["model_key"] in set(keys)]

    def set_missing_runs(self, item_id, value):
        self.missing_set[item_id] = value

    def mark_closed(self, item_id, price):
        self.closed[item_id] = price

    def last_reserved_price(self, item_id):
        return None

    def reserved_comps(self, model_key, since):
        return []

    def sold_comps(self, model_key, since):
        return []

    def upsert_model_price(self, row):
        self.model_writes.append(row)

    def sold_durations(self, model_key, since):
        return []

    def open_reserved_listings(self, keys):
        return [r for r in self._open if r.get("ever_reserved")]

    def purge_old_observations(self):
        self.purged = True

    def recent_runs(self, loop_name, limit):
        return self._runs[:limit]


class FakeClient:
    """Stands in for WallapopClient in both roles: the search context manager
    and the plain detail client the alert loop keeps open across candidates."""

    def __init__(self, items):
        self._items = items
        self.detail_calls = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass

    def close(self):
        pass

    def search(self, keywords, **kwargs):
        self.last_search_kwargs = kwargs
        yield from self._items

    def fetch_detail(self, item_id):
        self.detail_calls.append(item_id)
        for item in self._items:
            if item.item_id == item_id:
                return item
        return None

    def is_alive(self, item_id):
        return any(i.item_id == item_id for i in self._items)


class FakeTelegram:
    def __init__(self, *a, **k):
        self.sent = []
        self.errors = []

    def send_alert(self, item, deal, kind, previous_price=None, model_display=None):
        self.sent.append((item.item_id, kind, item.price, deal))
        return True

    def send_error(self, text):
        self.errors.append(text)

    def close(self):
        pass


ALERT_SEARCH = {
    "label": "RTX 3070",
    "role": "alert",
    "keywords": "rtx 3070",
    "model_key": "rtx_3070",
    "category_ids": config.CATEGORY_GPU,
    "max_price": 200,
    "distance_km": 100,
}

PRICES = {
    "rtx_3070": {
        "model_key": "rtx_3070",
        "ref_price": 300.0,
        "buy_ceiling": 220.0,
        "buy_ceiling_in_person": 250.0,
        "n_comps": 12,
        "is_seed": False,
    }
}


@pytest.fixture
def wire(monkeypatch):
    """Patch the loops' collaborators and hand back the fakes."""

    def _wire(module, db, items):
        tg = FakeTelegram()
        monkeypatch.setattr(module, "Database", lambda *a, **k: db)
        monkeypatch.setattr(module, "Telegram", lambda *a, **k: tg)
        monkeypatch.setattr(module, "WallapopClient", lambda *a, **k: FakeClient(items))
        monkeypatch.setattr(config, "require_secrets", lambda *a: None)
        return tg

    return _wire


# ------------------------------------------------------------------ alerts
def test_cheap_card_alerts_and_is_recorded(wire):
    db = FakeDB([ALERT_SEARCH], PRICES)
    tg = wire(alert_loop, db, [make_item("a1", "RTX 3070 Gigabyte OC", 180.0)])

    stats = alert_loop.run_once()

    assert stats["alerts_sent"] == 1
    assert tg.sent[0][0] == "a1" and tg.sent[0][1] == "new"
    assert db.recorded == [("a1", 180.0, "new")]


def test_dry_run_never_writes_to_the_dedup_table(wire, monkeypatch):
    """Regression: DRY_RUN skips the real Telegram send but FakeTelegram (and
    the real Telegram class) still returns True for logging purposes. If that
    True were treated as a real send, db.record_alert() would mark the item as
    already-alerted — silently suppressing the real alert once this actually
    runs for real. A production incident: local DRY_RUN testing polluted
    sent_alerts with 16 fake rows, none of which ever reached Telegram.
    """
    monkeypatch.setattr(config, "DRY_RUN", True)
    db = FakeDB([ALERT_SEARCH], PRICES)
    tg = wire(alert_loop, db, [make_item("a11", "RTX 3070 Gigabyte OC", 180.0)])

    stats = alert_loop.run_once()

    assert stats["alerts_sent"] == 1   # still visible/counted for the dry-run log
    assert tg.sent[0][0] == "a11"
    assert db.recorded == []           # but never persisted to the dedup table


def test_card_still_above_ceiling_even_after_a_haggle_is_silent(wire):
    # ref=300, ceiling=220. Even a 20%-off offer (280*0.8=224) stays above it.
    db = FakeDB([ALERT_SEARCH], PRICES)
    wire(alert_loop, db, [make_item("a2", "RTX 3070 Asus", 280.0)])
    assert alert_loop.run_once()["alerts_sent"] == 0


def test_reserved_listing_is_never_alerted_but_is_still_observed(wire):
    db = FakeDB([ALERT_SEARCH], PRICES)
    wire(alert_loop, db, [make_item("a3", "RTX 3070 MSI", 150.0, reserved=True)])

    assert alert_loop.run_once()["alerts_sent"] == 0
    # It still prices the market, which is the whole point of tracking reserved.
    assert db.observations[0]["status"] == "reserved"
    assert db.observations[0]["model_key"] == "rtx_3070"


def test_same_listing_same_price_is_not_resent(wire):
    db = FakeDB([ALERT_SEARCH], PRICES, alerted={"a4": [180.0]})
    wire(alert_loop, db, [make_item("a4", "RTX 3070 Gigabyte", 180.0)])
    assert alert_loop.run_once()["alerts_sent"] == 0


def test_price_drop_resends(wire):
    db = FakeDB([ALERT_SEARCH], PRICES, alerted={"a5": [190.0]})
    tg = wire(alert_loop, db, [make_item("a5", "RTX 3070 Gigabyte", 160.0)])

    assert alert_loop.run_once()["alerts_sent"] == 1
    assert tg.sent[0][1] == "price_drop"


def test_price_increase_does_not_resend(wire):
    db = FakeDB([ALERT_SEARCH], PRICES, alerted={"a6": [160.0]})
    wire(alert_loop, db, [make_item("a6", "RTX 3070 Gigabyte", 190.0)])
    assert alert_loop.run_once()["alerts_sent"] == 0


def test_junk_is_filtered_and_logged(wire):
    db = FakeDB([ALERT_SEARCH], PRICES)
    wire(alert_loop, db, [make_item("a7", "RTX 3070 no funciona para piezas", 60.0)])

    stats = alert_loop.run_once()
    assert stats["alerts_sent"] == 0
    assert db.junk[0]["category"] == "DEFECT"


def test_laptop_with_a_matching_gpu_never_alerts(wire):
    db = FakeDB([ALERT_SEARCH], PRICES)
    wire(alert_loop, db, [make_item("a8", "Lenovo Legion 5 RTX 3070", 190.0)])

    assert alert_loop.run_once()["alerts_sent"] == 0
    assert db.junk[0]["category"] == "LAPTOP"


def test_irrelevant_result_under_the_cap_is_dropped(wire):
    """Wallapop's loose matching returns CPUs for a GPU search."""
    db = FakeDB([ALERT_SEARCH], PRICES)
    wire(alert_loop, db, [make_item("a9", "AMD Ryzen 7 7800X3D precintado", 190.0)])

    assert alert_loop.run_once()["alerts_sent"] == 0
    assert db.junk == []  # not junk, just not what we searched for


def test_card_qualifies_via_offer_even_when_asking_is_above_the_ceiling(wire):
    """The offer-based gate: asking can sit above the raw ceiling and still
    qualify, as long as a realistic 20% haggle would clear it."""
    search = dict(ALERT_SEARCH, label="RTX 3070", keywords="rtx 3070",
                  model_key="rtx_3070", max_price=None)
    prices = {"rtx_3070": {"ref_price": 300.0, "buy_ceiling": 220.0,
                           "buy_ceiling_in_person": 250.0, "n_comps": 9, "is_seed": False}}
    db = FakeDB([search], prices)
    tg = wire(alert_loop, db, [make_item("a10", "RTX 3070 Gigabyte", 270.0)])
    # asking 270 > ceiling 220, but offer 270*0.8=216 <= 220 -> qualifies.
    assert alert_loop.run_once()["alerts_sent"] == 1
    assert tg.sent[0][2] == 270.0


def test_expensive_card_never_alerts_however_good_the_margin(wire):
    """MAX_ALERT_PRICE is a hard scope cap on the asking price, applied before
    any margin maths. A 4090 at 1100 with a 1050 ceiling would clear the offer
    gate comfortably, and still must not fire."""
    search = dict(ALERT_SEARCH, label="RTX 4090", keywords="rtx 4090",
                  model_key="rtx_4090", max_price=None)
    prices = {"rtx_4090": {"ref_price": 1200.0, "buy_ceiling": 1050.0,
                           "buy_ceiling_in_person": 1150.0, "n_comps": 9, "is_seed": False}}
    db = FakeDB([search], prices)
    tg = wire(alert_loop, db, [make_item("a11", "RTX 4090 Gigabyte", 1100.0)])

    stats = alert_loop.run_once()
    assert stats["alerts_sent"] == 0
    assert stats["over_cap"] == 1
    assert tg.sent == []


def test_underpriced_high_end_card_still_gets_through_the_cap(wire):
    """The flip side, and the whole reason the cap is on price rather than on
    model tier: a 4090 well under the cap is exactly what we want to catch, and
    the cap must not reject it for being a high-tier model."""
    search = dict(ALERT_SEARCH, label="RTX 4080", keywords="rtx 4080",
                  model_key="rtx_4080", max_price=None)
    prices = {"rtx_4080": {"ref_price": 620.0, "buy_ceiling": 525.0,
                           "buy_ceiling_in_person": 570.0, "n_comps": 9, "is_seed": False}}
    db = FakeDB([search], prices)
    tg = wire(alert_loop, db, [make_item("a12", "RTX 4080 Gigabyte", 300.0)])

    assert alert_loop.run_once()["alerts_sent"] == 1
    assert tg.sent[0][2] == 300.0


def test_implausibly_cheap_listing_is_treated_as_fraud_not_as_a_deal(wire):
    """A deliberate trade-off, and the one place the margin engine is
    structurally blind: the more absurd a price is, the better the margin it
    computes, so fakes sort straight to the top of the feed. A 4090 at 340
    against a 1200 reference is a scam, a dead card or bait essentially every
    time — genuine mispricing that extreme is vanishingly rare and gone in
    seconds anyway.

    This does cost the occasional real steal. MIN_PLAUSIBLE_RATIO is the knob:
    lower it to buy back the tail, at the price of fraud in the feed.
    """
    search = dict(ALERT_SEARCH, label="RTX 4090", keywords="rtx 4090",
                  model_key="rtx_4090", max_price=None)
    prices = {"rtx_4090": {"ref_price": 1200.0, "buy_ceiling": 1050.0,
                           "buy_ceiling_in_person": 1150.0, "n_comps": 9, "is_seed": False}}
    db = FakeDB([search], prices)
    tg = wire(alert_loop, db, [make_item("a13", "RTX 4090 Gigabyte", 340.0)])

    assert alert_loop.run_once()["alerts_sent"] == 0
    assert tg.sent == []


def test_bottom_condition_tier_is_blocked_but_fair_is_not(wire):
    """The dead-card filter. Only `has_given_it_all` is blocked — `fair` is a
    working card with a cosmetic flaw, which is exactly what a flip is."""
    db = FakeDB([ALERT_SEARCH], PRICES)
    tg = wire(
        alert_loop,
        db,
        [
            make_item("dead", "RTX 3070 Gigabyte", 180.0, condition="has_given_it_all"),
            make_item("worn", "RTX 3070 Asus Dual", 180.0, condition="fair"),
        ],
    )

    stats = alert_loop.run_once()
    assert stats["blocked_condition"] == 1
    assert [s[0] for s in tg.sent] == ["worn"]


def test_whole_machine_price_never_enters_a_comps_pool(wire):
    """A prebuilt that names a card is a real listing but not a comp for the
    loose card — its observation must carry no model_key."""
    db = FakeDB([ALERT_SEARCH], PRICES)
    wire(
        alert_loop,
        db,
        [make_item("pc1", "RTX 3070 Gigabyte", 300.0, taxonomy=(24200, 24203, 24115, 24117))],
    )

    alert_loop.run_once()
    assert [o["model_key"] for o in db.observations] == [None]


# ------------------------------------------------------------- sale inference
def test_reserved_item_needs_two_misses_before_counting_as_sold():
    db = FakeDB(
        [],
        open_listings=[
            {
                "item_id": "s1",
                "model_key": "rtx_3070",
                "last_price": 200,
                "last_status": "reserved",
                "ever_reserved": True,
                "missing_runs": 0,
                "title": "RTX 3070",
            }
        ],
    )
    # First miss: counted, not closed.
    assert comps_loop.infer_sales(db, {"rtx_3070"}, seen_ids=set()) == 0
    assert db.missing_set["s1"] == 1
    assert db.closed == {}

    db._open[0]["missing_runs"] = 1
    assert comps_loop.infer_sales(db, {"rtx_3070"}, seen_ids=set()) == 1
    assert db.closed["s1"] == 200


def test_item_seen_again_resets_its_miss_counter():
    db = FakeDB(
        [],
        open_listings=[
            {
                "item_id": "s2",
                "model_key": "rtx_3070",
                "last_price": 200,
                "last_status": "reserved",
                "ever_reserved": True,
                "missing_runs": 1,
                "title": "RTX 3070",
            }
        ],
    )
    comps_loop.infer_sales(db, {"rtx_3070"}, seen_ids={"s2"})
    assert db.missing_set["s2"] == 0
    assert db.closed == {}


def test_split_vram_children_are_covered_by_a_generic_comps_search(wire):
    """Regression: a generic 'rtx 4060 ti' search must also cover its 8g/16g
    children. classify() can resolve a listing to rtx_4060_ti_8g even though
    only the generic key has a search row — without expanding the covered set
    through GENERIC_FALLBACKS, that listing's closure (and its sold comp)
    would never be detected.
    """
    search = {
        "label": "Comps RTX 4060 TI",
        "role": "comps",
        "keywords": "rtx 4060 ti",
        "model_key": "rtx_4060_ti",
        "category_ids": config.CATEGORY_GPU,
        "max_price": None,
    }
    db = FakeDB(
        [search],
        open_listings=[
            {
                "item_id": "c1",
                "model_key": "rtx_4060_ti_8g",
                "last_price": 260,
                "last_status": "reserved",
                "ever_reserved": True,
                "missing_runs": 1,  # one miss already recorded last cycle
                "title": "RTX 4060 Ti 8GB",
            }
        ],
    )
    wire(comps_loop, db, [])  # nothing returned this cycle -> c1 stays absent

    stats = comps_loop.run_once()

    assert stats["closed"] == 1
    assert db.closed["c1"] == 260


def test_never_reserved_item_closes_without_a_sold_price():
    """Vanishing without ever being reserved tells us nothing about price."""
    db = FakeDB(
        [],
        open_listings=[
            {
                "item_id": "s3",
                "model_key": "rtx_3070",
                "last_price": 200,
                "last_status": "active",
                "ever_reserved": False,
                "missing_runs": 1,
                "title": "RTX 3070",
            }
        ],
    )
    assert comps_loop.infer_sales(db, {"rtx_3070"}, seen_ids=set()) == 1
    assert db.closed["s3"] is None


# ------------------------------------------------------- per-family price cap
PHONE_SEARCH = {
    "label": "Alert IPHONE 15 PRO",
    "role": "alert",
    "keywords": "iphone 15 pro",
    "model_key": "iphone_15_pro",
    "category_ids": config.CATEGORY_PHONE,
    "max_price": 500,
    "distance_km": None,
}

PHONE_PRICES = {
    "iphone_15_pro": {
        "model_key": "iphone_15_pro",
        "ref_price": 500.0,
        "buy_ceiling": 414.0,
        "buy_ceiling_in_person": 450.0,
        "n_comps": 11,
        "is_seed": False,
    }
}


def test_phone_priced_above_the_gpu_cap_still_alerts(wire):
    """The GPU cap is 350 and a used iPhone 15 Pro is ~550, so a single global
    cap would silently mute the entire phone category rather than filter it."""
    db = FakeDB([PHONE_SEARCH], PHONE_PRICES)
    tg = wire(alert_loop, db, [make_item("p1", "iPhone 15 Pro 128GB Titanio", 480.0)])

    stats = alert_loop.run_once()
    assert stats["over_cap"] == 0
    assert stats["alerts_sent"] == 1
    assert tg.sent[0][2] == 480.0


def test_phone_above_its_own_cap_does_not_alert(wire):
    db = FakeDB([PHONE_SEARCH], PHONE_PRICES)
    # Same model as the search, so it survives _relevant() and the cap is what
    # actually stops it — a Pro Max here would be filtered as irrelevant first
    # and the test would pass for the wrong reason.
    tg = wire(alert_loop, db, [make_item("p2", "iPhone 15 Pro 1TB", 1400.0)])

    stats = alert_loop.run_once()
    assert stats["over_cap"] == 1
    assert stats["alerts_sent"] == 0
    assert tg.sent == []


def test_gpu_cap_is_unchanged_by_the_phone_cap(wire):
    """A 400 EUR card is still over the GPU ceiling even though it sits well
    under the phone one — the cap follows the family, not the listing."""
    db = FakeDB([ALERT_SEARCH], PRICES)
    tg = wire(alert_loop, db, [make_item("g1", "RTX 3070 Gigabyte OC", 400.0)])

    stats = alert_loop.run_once()
    assert stats["over_cap"] == 1
    assert tg.sent == []
