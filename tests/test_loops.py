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


def make_item(item_id, title, price, reserved=False, description=""):
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
    )


class FakeDB:
    def __init__(self, searches, model_prices=None, alerted=None, open_listings=None):
        self._searches = searches
        self._model_prices = model_prices or {}
        self._alerted = alerted or {}
        self._open = open_listings or []
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


class FakeClient:
    def __init__(self, items):
        self._items = items

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass

    def search(self, keywords, **kwargs):
        yield from self._items


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


def test_card_above_the_ceiling_is_silent(wire):
    db = FakeDB([ALERT_SEARCH], PRICES)
    wire(alert_loop, db, [make_item("a2", "RTX 3070 Asus", 240.0)])
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


def test_hard_budget_ceiling_blocks_expensive_cards(wire, monkeypatch):
    monkeypatch.setattr(config, "MAX_DEAL_PRICE", 350.0)
    search = dict(ALERT_SEARCH, label="RTX 4090", keywords="rtx 4090",
                  model_key="rtx_4090", max_price=900)
    prices = {"rtx_4090": {"ref_price": 1200.0, "buy_ceiling": 1050.0,
                           "buy_ceiling_in_person": 1150.0, "n_comps": 9, "is_seed": False}}
    db = FakeDB([search], prices)
    wire(alert_loop, db, [make_item("a10", "RTX 4090 Gigabyte", 700.0)])

    assert alert_loop.run_once()["alerts_sent"] == 0


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
