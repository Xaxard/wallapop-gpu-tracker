"""Thin, defensive client for Wallapop's internal search API.

The v3 search endpoint changes shape between releases, so both the envelope
(where the item array lives) and the item fields are read through fallback
chains rather than fixed paths. If a fetch starts returning 403, open
wallapop.es with DevTools -> Network, copy the headers off a real
`search?...` request, and update HEADERS below.
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass
from typing import Any, Iterator

import httpx

import config

log = logging.getLogger("wallapop")

SEARCH_URL = "https://api.wallapop.com/api/v3/search"

# Verified against the live API 2026-08-01: `source` is mandatory — without it
# every request is a bare 400 regardless of the other params.
REQUIRED_PARAMS = {"source": "search_box"}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
    "X-DeviceOS": "0",
    "Origin": "https://es.wallapop.com",
    "Referer": "https://es.wallapop.com/",
}


@dataclass
class Item:
    item_id: str
    title: str
    description: str
    price: float | None
    currency: str
    web_url: str
    image_url: str | None
    reserved: bool
    shipping: bool
    location: str | None
    distance_km: float | None

    @property
    def status(self) -> str:
        return "reserved" if self.reserved else "active"


# --------------------------------------------------------------- extraction
def _first(d: Any, *paths: str, default: Any = None) -> Any:
    """Read the first path that resolves. Paths are dotted, e.g. 'price.amount'."""
    for path in paths:
        cur = d
        ok = True
        for part in path.split("."):
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            else:
                ok = False
                break
        if ok and cur not in (None, ""):
            return cur
    return default


def extract_items(payload: Any) -> list[dict]:
    """Find the item array wherever this API version decided to put it."""
    if not isinstance(payload, dict):
        return []
    candidates = (
        "search_objects",
        "items",
        "data.section.payload.items",
        "data.items",
        "section.payload.items",
        "payload.items",
    )
    for path in candidates:
        found = _first(payload, path)
        if isinstance(found, list) and found:
            return [x for x in found if isinstance(x, dict)]
    # Last resort: some versions wrap each hit as {"type":..,"content":{...}}
    for value in payload.values():
        if isinstance(value, list) and value and isinstance(value[0], dict):
            if any(k in value[0] for k in ("id", "item_id", "content")):
                return value
    return []


def _unwrap(raw: dict) -> dict:
    """Some shapes nest the real item under 'content' or 'item'."""
    for key in ("content", "item"):
        inner = raw.get(key)
        if isinstance(inner, dict) and ("id" in inner or "title" in inner):
            return inner
    return raw


def _price(raw: dict) -> tuple[float | None, str]:
    value = _first(raw, "price.amount", "price", "sale_price", "price_amount")
    currency = _first(raw, "price.currency", "currency", default="EUR")
    if isinstance(value, dict):  # nested one level deeper in some versions
        currency = value.get("currency", currency)
        value = value.get("amount")
    try:
        return (float(value), str(currency)) if value is not None else (None, str(currency))
    except (TypeError, ValueError):
        return None, str(currency)


def _image(raw: dict) -> str | None:
    images = raw.get("images") or raw.get("image") or []
    if isinstance(images, dict):
        images = [images]
    if isinstance(images, list):
        for img in images:
            if isinstance(img, str):
                return img
            if isinstance(img, dict):
                url = _first(
                    img,
                    "urls.big",
                    "urls.medium",
                    "urls.original",
                    "urls.small",
                    "original",
                    "big",
                    "medium",
                    "url",
                )
                if url:
                    return str(url)
    return _first(raw, "main_image.urls.big", "main_image.urls.medium", "main_image.url")


def _flag(raw: dict, *paths: str) -> bool:
    """Read a boolean that the API may wrap as {"flag": true}.

    The live shape is `"reserved": {"flag": false}` — reading the bare key would
    make every listing truthy, so the wrapped path is tried first.
    """
    for path in paths:
        val = _first(raw, path)
        if isinstance(val, dict):
            val = val.get("flag")
        if isinstance(val, bool):
            return val
        if val is not None:
            return bool(val)
    return False


def _reserved(raw: dict) -> bool:
    return _flag(raw, "reserved.flag", "reserved", "flags.reserved", "is_reserved")


def _shipping(raw: dict) -> bool:
    return _flag(
        raw,
        "shipping.item_is_shippable",
        "shipping_allowed",
        "flags.shipping_allowed",
    )


def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in km."""
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _distance_km(raw: dict) -> float | None:
    """Distance from the configured centre.

    The API doesn't return one, but every listing carries its own coordinates,
    so it's computed locally rather than left blank in the alert.
    """
    direct = _first(raw, "distance", "location.distance")
    if isinstance(direct, (int, float)):
        return float(direct)
    lat = _first(raw, "location.latitude")
    lon = _first(raw, "location.longitude")
    if isinstance(lat, (int, float)) and isinstance(lon, (int, float)):
        return round(_haversine(config.LATITUDE, config.LONGITUDE, float(lat), float(lon)), 1)
    return None


def parse_item(raw: dict) -> Item | None:
    raw = _unwrap(raw)
    item_id = _first(raw, "id", "item_id", "item_uuid")
    if not item_id:
        return None
    slug = _first(raw, "web_slug", "slug", "share_url")
    if slug and str(slug).startswith("http"):
        web_url = str(slug)
    elif slug:
        web_url = f"https://es.wallapop.com/item/{slug}"
    else:
        web_url = f"https://es.wallapop.com/item/{item_id}"
    price, currency = _price(raw)
    return Item(
        item_id=str(item_id),
        title=str(_first(raw, "title", "name", default="") or ""),
        description=str(_first(raw, "description", "body", default="") or ""),
        price=price,
        currency=currency,
        web_url=web_url,
        image_url=_image(raw),
        reserved=_reserved(raw),
        shipping=_shipping(raw),
        location=_first(raw, "location.city", "location.name", "user.location.city"),
        distance_km=_distance_km(raw),
    )


# --------------------------------------------------------------- the client
class WallapopClient:
    def __init__(self, client: httpx.Client | None = None) -> None:
        self._client = client or httpx.Client(
            headers=HEADERS, timeout=config.HTTP_TIMEOUT, follow_redirects=True
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "WallapopClient":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # Rate-limited/blocked and transient server errors are worth a backoff
    # retry; any other status (400, 404, ...) means the request itself is
    # wrong and retrying it changes nothing.
    _RETRYABLE_STATUS = {403, 429, 500, 502, 503, 504}

    def _get(self, params: dict) -> dict | None:
        """One request with backoff. Returns None once retries are exhausted."""
        for attempt in range(1, config.HTTP_RETRIES + 1):
            try:
                resp = self._client.get(SEARCH_URL, params=params)
                if resp.status_code == 200:
                    return resp.json()
                if resp.status_code in self._RETRYABLE_STATUS:
                    wait = 2 ** attempt
                    log.warning(
                        "HTTP %s from Wallapop (attempt %d/%d) — backing off %ds",
                        resp.status_code, attempt, config.HTTP_RETRIES, wait,
                    )
                    time.sleep(wait)
                    continue
                log.warning("HTTP %s from Wallapop: %s", resp.status_code, resp.text[:200])
                return None
            except (httpx.HTTPError, ValueError) as exc:
                log.warning("Request failed (attempt %d/%d): %s", attempt, config.HTTP_RETRIES, exc)
                time.sleep(2 ** attempt)
        return None

    def search(
        self,
        keywords: str,
        *,
        min_price: float | None = None,
        max_price: float | None = None,
        category_ids: str | None = None,
        distance_km: int | None = None,
        order_by: str = "newest",
        max_pages: int = 1,
        nationwide: bool = False,
    ) -> Iterator[Item]:
        """Yield parsed items, following pagination up to max_pages.

        `nationwide=True` drops the geo filter entirely, which is what the comps
        loop wants: the widest possible price distribution per model.
        """
        base: dict[str, Any] = {**REQUIRED_PARAMS, "keywords": keywords, "order_by": order_by}
        if not nationwide:
            base["latitude"] = config.LATITUDE
            base["longitude"] = config.LONGITUDE
            base["distance_in_km"] = distance_km or config.DEFAULT_DISTANCE_KM
        if min_price is not None:
            base["min_sale_price"] = int(min_price)
        if max_price is not None:
            base["max_sale_price"] = int(max_price)
        if category_ids:
            base["category_ids"] = category_ids

        seen: set[str] = set()
        next_token: str | None = None
        offset = 0

        for page in range(max_pages):
            params = dict(base)
            if next_token:
                params["next_page"] = next_token
            elif offset:
                params["start"] = offset

            payload = self._get(params)
            if payload is None:
                return

            raws = extract_items(payload)
            if not raws:
                if page == 0:
                    log.warning(
                        "No items parsed for %r — response keys: %s",
                        keywords,
                        list(payload)[:8] if isinstance(payload, dict) else type(payload),
                    )
                return

            emitted = 0
            for raw in raws:
                item = parse_item(raw)
                if item is None or item.item_id in seen:
                    continue
                seen.add(item.item_id)
                emitted += 1
                yield item

            next_token = _first(payload, "meta.next_page", "next_page", "meta.cursor")
            offset += len(raws)
            if not next_token and emitted == 0:
                return
            if page + 1 < max_pages:
                time.sleep(config.REQUEST_DELAY)
