"""Telegram caption formatting, especially the 1024-char truncation edge case."""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from alerts import CAPTION_LIMIT, build_caption  # noqa: E402
from pricing import Deal  # noqa: E402
from wallapop_client import Item  # noqa: E402

DEAL = Deal(
    qualifies=True,
    reason="offer clears buy ceiling",
    ref_price=330.0,
    ceiling_shipped=224.0,
    ceiling_in_person=280.0,
    offer_price=168.0,
    net_shipped=55.0,
    net_in_person=120.0,
    net_shipped_at_asking=19.0,
    is_seed=False,
    n_comps=12,
)


def make_item(**overrides):
    base = dict(
        item_id="x1",
        title="RTX 4070 Gigabyte Gaming OC",
        description="Estado impecable, poco uso, con caja y factura original.",
        price=210.0,
        currency="EUR",
        web_url="https://es.wallapop.com/item/rtx-4070-gigabyte-123",
        image_url="https://cdn/x.jpg",
        reserved=False,
        shipping=True,
        location="Madrid",
        distance_km=4.0,
        country="ES",
    )
    base.update(overrides)
    return Item(**base)


def has_unbalanced_tags(text: str) -> bool:
    """True if any <b>/<s>/<i> tag has no matching close in the same text."""
    for tag in ("b", "s", "i"):
        opens = len(re.findall(rf"<{tag}>", text))
        closes = len(re.findall(rf"</{tag}>", text))
        if opens != closes:
            return True
    return False


def test_normal_caption_is_well_formed():
    caption = build_caption(make_item(), DEAL, "new", None, "RTX 4070")
    assert len(caption) <= CAPTION_LIMIT
    assert not has_unbalanced_tags(caption)
    assert "RTX 4070 Gigabyte Gaming OC" in caption


def test_long_description_is_trimmed_not_the_link():
    item = make_item(description="x" * 5000)
    caption = build_caption(item, DEAL, "new", None, "RTX 4070")
    assert len(caption) <= CAPTION_LIMIT
    assert not has_unbalanced_tags(caption)
    assert item.web_url in caption  # link survives even when description is huge


def test_extreme_overflow_falls_back_to_safe_line_truncation():
    """A location/title long enough to overflow even with an empty description
    must still produce a caption Telegram can parse — no tag split mid-cut.
    """
    item = make_item(
        title="RTX 4070 " + "Z" * 400,
        location="M" * 400,
        description="",
    )
    caption = build_caption(item, DEAL, "new", None, "RTX 4070")
    assert len(caption) <= CAPTION_LIMIT + 1  # +1 for the trailing ellipsis char
    assert not has_unbalanced_tags(caption)


def test_price_drop_shows_strikethrough_previous():
    caption = build_caption(make_item(price=180.0), DEAL, "price_drop", 210.0, "RTX 4070")
    assert "<s>210€</s>" in caption
    assert not has_unbalanced_tags(caption)


def test_new_listing_never_shows_a_previous_price():
    caption = build_caption(make_item(), DEAL, "new", None, "RTX 4070")
    assert "<s>" not in caption


def test_html_special_characters_in_title_are_escaped():
    item = make_item(title="RTX 4070 <script>alert(1)</script> & Co")
    caption = build_caption(item, DEAL, "new", None, "RTX 4070")
    assert "<script>" not in caption
    assert "&lt;script&gt;" in caption


def test_offer_price_is_shown_for_a_priced_deal():
    caption = build_caption(make_item(), DEAL, "new", None, "RTX 4070")
    assert "168€" in caption  # the suggested offer, not just the asking price


def test_foreign_country_is_shown_in_location_line():
    caption = build_caption(make_item(country="IT", location="Milano"), DEAL, "new", None, "RTX 4070")
    assert "IT" in caption


def test_spain_is_not_called_out_as_foreign():
    caption = build_caption(make_item(country="ES"), DEAL, "new", None, "RTX 4070")
    location_line = next(line for line in caption.splitlines() if line.startswith("📍"))
    segments = [s.strip() for s in location_line.split("·")]
    assert "ES" not in segments
