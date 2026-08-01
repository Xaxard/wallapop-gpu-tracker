"""Live sanity check — hits the real Wallapop API, touches no database.

    python smoke_test.py "rtx 4070" [most_relevance|newest]

Prints the raw response envelope keys (so you can see immediately if the API
changed shape) followed by the parsed, classified, junk-filtered items. Run
this first whenever alerts go quiet.
"""

from __future__ import annotations

import json
import sys

import config
import junk
import models
import pricing
from wallapop_client import REQUIRED_PARAMS, SEARCH_URL, WallapopClient, extract_items


def main() -> int:
    config.setup_logging()
    keywords = sys.argv[1] if len(sys.argv) > 1 else "rtx 4070"
    order_by = sys.argv[2] if len(sys.argv) > 2 else "most_relevance"

    with WallapopClient() as wp:
        payload = wp._get(
            {
                **REQUIRED_PARAMS,
                "keywords": keywords,
                "order_by": order_by,
                "latitude": config.LATITUDE,
                "longitude": config.LONGITUDE,
                "distance_in_km": config.DEFAULT_DISTANCE_KM,
                "category_ids": config.CATEGORY_GPU,
            }
        )
        if payload is None:
            print(f"FAILED: no usable response from {SEARCH_URL}")
            print("If this is a 403, copy fresh headers from DevTools into wallapop_client.HEADERS")
            return 1

        print(f"envelope keys: {list(payload)}")
        raws = extract_items(payload)
        print(f"items found in envelope: {len(raws)}")
        if raws:
            print("\n--- first raw item (truncated) ---")
            print(json.dumps(raws[0], ensure_ascii=False, indent=2)[:1500])

        print("\n--- parsed ---")
        for item in wp.search(
            keywords,
            category_ids=config.CATEGORY_GPU,
            order_by=order_by,
            max_pages=1,
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
