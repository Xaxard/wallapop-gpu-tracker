"""The response parser must survive every envelope shape we've seen."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

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
            "location": {"city": "Madrid"},
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
