"""The response parser must survive every envelope shape we've seen."""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
import wallapop_client as wc  # noqa: E402


def test_extract_items_search_objects():
    payload = {"search_objects": [{"id": "1"}, {"id": "2"}]}
    assert len(wc.extract_items(payload)) == 2


def test_extract_items_nested_section_payload():
    payload = {"data": {"section": {"payload": {"items": [{"id": "a"}]}}}}
    assert wc.extract_items(payload)[0]["id"] == "a"


def test_extract_items_flat_items():
    assert wc.extract_items({"items": [{"id": "z"}]})[0]["id"] == "z"


def test_extract_items_on_junk_returns_empty():
    assert wc.extract_items({"meta": {"next_page": None}}) == []
    assert wc.extract_items("not a dict") == []


def test_parse_item_modern_shape():
    item = wc.parse_item(
        {
            "id": "abc123",
            "title": "RTX 4070 Gigabyte",
            "description": "Como nueva",
            "price": {"amount": 320.5, "currency": "EUR"},
            "web_slug": "rtx-4070-gigabyte-123",
            "images": [{"urls": {"big": "https://cdn/img-big.jpg"}}],
            "flags": {"reserved": True},
            "shipping_allowed": True,
            "location": {"city": "Madrid", "country_code": "ES"},
            "distance": 4.2,
        }
    )
    assert item.item_id == "abc123"
    assert item.price == 320.5
    assert item.web_url == "https://es.wallapop.com/item/rtx-4070-gigabyte-123"
    assert item.image_url == "https://cdn/img-big.jpg"
    assert item.reserved is True
    assert item.status == "reserved"
    assert item.location == "Madrid"
    assert item.country == "ES"


def test_parse_item_extracts_foreign_country():
    item = wc.parse_item(
        {
            "id": "it1",
            "title": "RTX 4070 Milano",
            "location": {"city": "Milano", "country_code": "IT"},
        }
    )
    assert item.country == "IT"


def test_parse_item_legacy_flat_price_and_wrapper():
    item = wc.parse_item(
        {
            "type": "item",
            "content": {
                "id": "old1",
                "title": "RX 7800 XT",
                "price": 400,
                "currency": "EUR",
                "images": ["https://cdn/legacy.jpg"],
            },
        }
    )
    assert item.item_id == "old1"
    assert item.price == 400.0
    assert item.image_url == "https://cdn/legacy.jpg"
    assert item.reserved is False
    assert item.web_url.endswith("/item/old1")  # no slug -> id fallback


def test_parse_item_without_id_is_dropped():
    assert wc.parse_item({"title": "no id here"}) is None


def test_parse_item_tolerates_missing_price():
    item = wc.parse_item({"id": "x", "title": "t"})
    assert item.price is None
    assert item.image_url is None


def test_parse_item_new_fields_default_absent_on_search_shape():
    item = wc.parse_item({"id": "x", "title": "t"})
    assert item.condition is None
    assert item.brand is None
    assert item.taxonomy == ()
    assert item.posted_at is None
    assert item.modified_at is None
    assert item.user_allows_shipping is None
    assert item.age_seconds is None


# --------------------------------------------------------- taxonomy / whole_machine
def test_parse_item_taxonomy_coerces_int_and_string_ids():
    item = wc.parse_item(
        {
            "id": "1",
            "title": "t",
            "taxonomy": [
                {"id": 10304},          # int, as on /search
                {"id": "24115"},        # string, as on /items/{id} detail
                {"id": "not-a-number"}, # skipped
                {"notid": 5},           # missing id -> skipped
            ],
        }
    )
    assert item.taxonomy == (10304, 24115)


def test_parse_item_taxonomy_ignores_non_list():
    item = wc.parse_item({"id": "1", "title": "t", "taxonomy": {"id": 1}})
    assert item.taxonomy == ()


def test_whole_machine_true_when_taxonomy_hits_the_set():
    item = wc.parse_item({"id": "1", "title": "t", "taxonomy": [{"id": 24116}]})  # Portátiles gaming
    assert item.whole_machine is True


def test_whole_machine_false_for_components_only():
    item = wc.parse_item({"id": "1", "title": "t", "taxonomy": [{"id": config.TAXONOMY_COMPONENTS}]})
    assert item.whole_machine is False


# ----------------------------------------------------------------- timestamps
def test_parse_item_posted_and_modified_at_from_epoch_ms():
    posted_ms = 1_700_000_000_000  # 2023-11-14T22:13:20Z
    modified_ms = 1_700_100_000_000
    item = wc.parse_item(
        {"id": "1", "title": "t", "created_at": posted_ms, "modified_at": modified_ms}
    )
    assert item.posted_at == datetime.fromtimestamp(posted_ms / 1000, tz=timezone.utc)
    assert item.modified_at == datetime.fromtimestamp(modified_ms / 1000, tz=timezone.utc)
    assert item.posted_at.tzinfo is timezone.utc


@pytest.mark.parametrize(
    "created_at",
    [
        1,  # epoch-ms of ~1970 -> pre-2015 sanity floor, must not slip through
        -1,
        "not-a-number",
        None,
        99_999_999_999_999_999,  # absurdly far future
    ],
)
def test_parse_item_epoch_ms_guards_against_nonsense(created_at):
    item = wc.parse_item({"id": "1", "title": "t", "created_at": created_at})
    assert item.posted_at is None


def test_age_seconds_computed_from_posted_at():
    item = wc.Item(
        item_id="1",
        title="t",
        description="",
        price=None,
        currency="EUR",
        web_url="",
        image_url=None,
        reserved=False,
        shipping=False,
        location=None,
        distance_km=None,
        posted_at=datetime.now(timezone.utc) - timedelta(hours=2),
    )
    assert item.age_seconds == pytest.approx(7200, abs=5)


# ------------------------------------------------------------- condition / brand
def test_parse_item_extracts_condition_and_brand_from_type_attributes():
    item = wc.parse_item(
        {
            "id": "1",
            "title": "t",
            "type_attributes": {
                "condition": {"value": "as_good_as_new"},
                "brand": {"value": "NVIDIA"},
            },
        }
    )
    assert item.condition == "as_good_as_new"
    assert item.brand == "NVIDIA"


# --------------------------------------------------------- shipping divergence
def test_can_ship_prefers_user_allows_shipping_over_category_flag():
    item = wc.parse_item(
        {
            "id": "1",
            "title": "t",
            "shipping": {"item_is_shippable": True, "user_allows_shipping": False},
        }
    )
    assert item.shipping is True  # category capability, unchanged
    assert item.user_allows_shipping is False
    assert item.can_ship is False  # seller opted out despite the category allowing it


def test_can_ship_falls_back_to_shipping_when_user_flag_missing():
    item = wc.parse_item({"id": "1", "title": "t", "shipping_allowed": True})
    assert item.user_allows_shipping is None
    assert item.can_ship is True


# ----------------------------------------------------------- detail-shape parsing
def test_parse_item_detail_shape_title_description_price():
    item = wc.parse_item(
        {
            "id": "9",
            "title": {"original": "RTX 4080 Founders Edition"},
            "description": {"original": "Como nueva, poco uso"},
            "price": {"cash": {"amount": 899.0, "currency": "EUR"}},
            "type_attributes": {
                "condition": {"value": "as_good_as_new"},
                "brand": {"value": "NVIDIA"},
            },
            "taxonomy": [{"id": "10304"}],
            "shipping": {"item_is_shippable": True, "user_allows_shipping": True},
        }
    )
    assert item.title == "RTX 4080 Founders Edition"
    assert item.description == "Como nueva, poco uso"
    assert item.price == 899.0
    assert item.currency == "EUR"
    assert item.condition == "as_good_as_new"
    assert item.brand == "NVIDIA"
    assert item.taxonomy == (10304,)
    assert item.can_ship is True


# --------------------------------------------------------------- retry logic
class FakeResponse:
    def __init__(self, status_code, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload


class FakeHttpxClient:
    """Returns one canned response per call, in order."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

    def get(self, url, params=None):
        self.calls += 1
        return self._responses[min(self.calls, len(self._responses)) - 1]


@pytest.mark.parametrize("status", [403, 429, 500, 502, 503, 504])
def test_retryable_statuses_are_retried_then_succeed(monkeypatch, status):
    monkeypatch.setattr("time.sleep", lambda *_: None)
    monkeypatch.setattr(config, "HTTP_RETRIES", 3)
    fake = FakeHttpxClient([FakeResponse(status), FakeResponse(200, {"items": [{"id": "1"}]})])
    client = wc.WallapopClient(client=fake)

    result = client._get({"keywords": "rtx 4070"})

    assert result == {"items": [{"id": "1"}]}
    assert fake.calls == 2


@pytest.mark.parametrize("status", [400, 404])
def test_non_retryable_statuses_fail_immediately(monkeypatch, status):
    monkeypatch.setattr("time.sleep", lambda *_: None)
    monkeypatch.setattr(config, "HTTP_RETRIES", 3)
    fake = FakeHttpxClient([FakeResponse(status, text="bad request")])
    client = wc.WallapopClient(client=fake)

    result = client._get({"keywords": "rtx 4070"})

    assert result is None
    assert fake.calls == 1  # no retry wasted on a request that can't succeed


def test_exhausting_all_retries_returns_none(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda *_: None)
    monkeypatch.setattr(config, "HTTP_RETRIES", 3)
    fake = FakeHttpxClient([FakeResponse(503)])  # every attempt fails the same way
    client = wc.WallapopClient(client=fake)

    assert client._get({"keywords": "rtx 4070"}) is None
    assert fake.calls == 3


# ------------------------------------------------------------------ is_alive
def test_is_alive_true_on_200(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda *_: None)
    fake = FakeHttpxClient([FakeResponse(200, {"id": "1"})])
    client = wc.WallapopClient(client=fake)

    assert client.is_alive("1") is True


def test_is_alive_false_on_404():
    fake = FakeHttpxClient([FakeResponse(404, text="not found")])
    client = wc.WallapopClient(client=fake)

    assert client.is_alive("1") is False
    assert fake.calls == 1  # no retry — 404 isn't a transient failure


def test_is_alive_none_when_request_fails(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda *_: None)
    monkeypatch.setattr(config, "HTTP_RETRIES", 3)
    fake = FakeHttpxClient([FakeResponse(503)])  # retries exhausted, never resolves
    client = wc.WallapopClient(client=fake)

    # Must be None, not False — a network failure is not evidence the item sold.
    assert client.is_alive("1") is None


def test_is_alive_none_on_non_retryable_non_404_status():
    fake = FakeHttpxClient([FakeResponse(400, text="bad request")])
    client = wc.WallapopClient(client=fake)

    assert client.is_alive("1") is None


# ---------------------------------------------------------------- fetch_detail
def test_fetch_detail_parses_detail_shape():
    payload = {
        "id": "42",
        "title": {"original": "RTX 3080"},
        "price": {"cash": {"amount": 500.0, "currency": "EUR"}},
    }
    fake = FakeHttpxClient([FakeResponse(200, payload)])
    client = wc.WallapopClient(client=fake)

    item = client.fetch_detail("42")

    assert item is not None
    assert item.item_id == "42"
    assert item.title == "RTX 3080"
    assert item.price == 500.0


def test_fetch_detail_none_on_404():
    fake = FakeHttpxClient([FakeResponse(404)])
    client = wc.WallapopClient(client=fake)

    assert client.fetch_detail("42") is None


def test_fetch_detail_none_when_request_fails(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda *_: None)
    monkeypatch.setattr(config, "HTTP_RETRIES", 3)
    fake = FakeHttpxClient([FakeResponse(503)])
    client = wc.WallapopClient(client=fake)

    assert client.fetch_detail("42") is None


# ------------------------------------------------------------ search() params
class _CapturingClient:
    """Records the params of the single request `search()` issues."""

    def __init__(self, payload):
        self._payload = payload
        self.params: dict | None = None

    def get(self, url, params=None):
        self.params = params
        return FakeResponse(200, self._payload)


def test_search_sends_time_filter_and_condition_when_set():
    fake = _CapturingClient({"items": []})
    client = wc.WallapopClient(client=fake)

    list(client.search("rtx 4070", time_filter="lastWeek", condition="good"))

    assert fake.params["time_filter"] == "lastWeek"
    assert fake.params["condition"] == "good"


def test_search_omits_time_filter_and_condition_when_unset():
    fake = _CapturingClient({"items": []})
    client = wc.WallapopClient(client=fake)

    list(client.search("rtx 4070"))

    assert "time_filter" not in fake.params
    assert "condition" not in fake.params
