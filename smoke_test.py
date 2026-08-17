"""Live sanity check — hits the real Wallapop API, touches no database.

    python smoke_test.py "rtx 4070" [most_relevance|newest] [time_filter|none]

Prints the request it sent (including whether a `time_filter` went with it), the
raw response envelope keys (so you can see immediately if the API changed shape)
and then the parsed, classified, junk-filtered items. Run this first whenever
alerts go quiet.

The `time_filter` default is not cosmetic, and this script was actively
misleading without it. `time_filter` is the parameter config.py identifies as
load-bearing: it is what makes a query return anything at all when the request
geolocates outside Spain, and its absence is the direct cause of the original
day-long silent outage (the comps loop logged "0 items" on all 40 searches,
every hour, for over a day). Run from a non-Spanish IP — a GitHub Actions
runner, a VPS, a VPN — a smoke test that sent no time_filter came back empty
whether or not the bot was healthy, so the one tool the dead-man switch points
the operator at reproduced the failure it was meant to diagnose.

Pass `none` as the third argument to deliberately reproduce that: an empty
result with `none` and a full one with `lastWeek` is a positive identification
of the geolocation failure rather than a schema change.
"""

from __future__ import annotations

import json
import sys

import httpx

import config
import junk
import models
import pricing
from wallapop_client import (
    HEADERS,
    REQUIRED_PARAMS,
    SEARCH_URL,
    WallapopClient,
    extract_items,
)

# "none"/"off"/"" all disable the filter, for the reproduce-the-outage case.
NO_FILTER = {"none", "off", "no", ""}


def main() -> int:
    config.setup_logging()
    keywords = sys.argv[1] if len(sys.argv) > 1 else "rtx 4070"
    order_by = sys.argv[2] if len(sys.argv) > 2 else "most_relevance"
    raw_filter = sys.argv[3] if len(sys.argv) > 3 else config.ALERT_TIME_FILTER
    time_filter = None if str(raw_filter).strip().lower() in NO_FILTER else raw_filter

    params = {
        **REQUIRED_PARAMS,
        "keywords": keywords,
        "order_by": order_by,
        "latitude": config.LATITUDE,
        "longitude": config.LONGITUDE,
        "distance_in_km": config.DEFAULT_DISTANCE_KM,
        "category_ids": config.CATEGORY_GPU,
    }
    if time_filter:
        params["time_filter"] = time_filter

    print(
        f"time_filter: {time_filter or 'NOT SENT'}"
        + (
            ""
            if time_filter
            else "  <-- an empty result here proves nothing; a filterless query "
            "returns 200 OK with zero items whenever the request geolocates "
            "outside the marketplace"
        )
    )
    print(f"request params: {params}")

    # A plain request rather than WallapopClient's search helper, so the status
    # code reaches the operator instead of collapsing to None after three
    # backoff retries. Knowing it was a 403 (headers stale) and not a 400
    # (parameter rejected) is most of the diagnosis, and it is the thing the
    # retry logic the loops want was hiding here.
    try:
        resp = httpx.get(
            SEARCH_URL,
            params=params,
            headers=HEADERS,
            timeout=config.HTTP_TIMEOUT,
            follow_redirects=True,
        )
    except httpx.HTTPError as exc:
        print(f"FAILED: {SEARCH_URL} unreachable: {exc}")
        return 1

    print(f"HTTP {resp.status_code} from {SEARCH_URL}")
    if resp.status_code != 200:
        if resp.status_code == 403:
            print(
                "403 — copy fresh headers from DevTools into wallapop_client.HEADERS"
            )
        elif resp.status_code == 400:
            print(
                "400 — a parameter was rejected; time_filter only accepts "
                "today / lastWeek / lastMonth"
            )
        print(resp.text[:300])
        return 1

    try:
        payload = resp.json()
    except ValueError as exc:
        print(f"FAILED: malformed JSON from {SEARCH_URL}: {exc}")
        return 1

    print(f"envelope keys: {list(payload)}")
    raws = extract_items(payload)
    print(f"items found in envelope: {len(raws)}")
    if raws:
        print("\n--- first raw item (truncated) ---")
        print(json.dumps(raws[0], ensure_ascii=False, indent=2)[:1500])

    print("\n--- parsed ---")
    with WallapopClient() as wp:
        for item in wp.search(
            keywords,
            category_ids=config.CATEGORY_GPU,
            order_by=order_by,
            max_pages=1,
            time_filter=time_filter,
        ):
            verdict = junk.check(item.title, item.description)
            match = models.classify(item.title, item.description)
            flags = []
            if item.reserved:
                flags.append("RESERVED")
            if verdict.excluded:
                flags.append(f"JUNK:{verdict.category}({verdict.phrase})")
            if not pricing.sane(item.price):
                flags.append("PRICE-OUT-OF-BAND")
            where = f"{item.location or '?'} {item.distance_km if item.distance_km is not None else '?'}km"
            print(
                f"{str(item.price):>8} EUR  {(match.model_key or '-'):<18}"
                f"{match.confidence:<8}{item.title[:44]:<46}{where:<26}{' '.join(flags)}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
