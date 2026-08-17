"""Idempotent seeding of `searches` and the day-one `model_prices`.

Run once after applying schema.sql:  python seed.py
Safe to re-run — searches upsert on their label, and seed prices never
overwrite a reference the comps loop has already learned.
"""

from __future__ import annotations

import logging

import config
from db import Database, chunked, now

log = logging.getLogger("seed")

GPU = config.CATEGORY_GPU
RADIUS = config.DEFAULT_DISTANCE_KM

# ---------------------------------------------------------------- §7 config
# Deal-alert searches. max_price is only a coarse API-side filter to keep the
# volume down; once a model has >= MIN_COMPS the learned buy_ceiling is what
# actually decides.
ALERT_SEARCHES = [
    ("RTX 3060", "rtx 3060", "rtx_3060", 150),
    ("RTX 3060 Ti", "rtx 3060 ti", "rtx_3060_ti", 185),
    ("RTX 3070", "rtx 3070", "rtx_3070", 200),
    ("RTX 3070 Ti", "rtx 3070 ti", "rtx_3070_ti", 240),
    ("RTX 3080", "rtx 3080", "rtx_3080", 250),
    ("RTX 4060", "rtx 4060", "rtx_4060", 200),
    ("RTX 4060 Ti", "rtx 4060 ti", "rtx_4060_ti", 280),
    ("RTX 4070", "rtx 4070", "rtx_4070", 330),
    ("RTX 5060", "rtx 5060", "rtx_5060", 250),
    ("RX 6700 XT", "rx 6700 xt", "rx_6700_xt", 220),
    ("RX 6800", "rx 6800", "rx_6800", 260),
    ("RX 7600", "rx 7600", "rx_7600", 200),
    ("RX 9060 XT", "9060 xt", "rx_9060_xt", 350),
]

# Broad discovery: no bootstrap cap, so these only ever fire when the margin
# engine has a confident reference price and the listing clears the ceiling.
#
# These matter more than the model-targeted searches above, because they are
# the only way a *mispriced high-end* card is ever seen — nobody writes a
# search for "RTX 4090" expecting one at 340 EUR, but that is exactly the
# listing worth catching, and the price caps let it through on price while the
# margin gate proves it is real.
#
# Measured hit rates per 120 results (2026-08-02): "rtx" 37 classified,
# "tarjeta grafica" 20, "grafica" 11, "rx" 5, "amd" 5. The weak ones are kept
# because a page costs ~0.2s and they cover AMD listings the others miss.
DISCOVERY_SEARCHES = [
    ("Discovery RTX", "rtx"),
    ("Discovery RX", "rx"),
    ("Discovery AMD", "amd"),
    ("Discovery Nvidia", "nvidia"),
    ("Discovery GeForce", "geforce"),
    ("Discovery Radeon", "radeon"),
    ("Discovery GPU", "gpu"),
    ("Discovery Tarjeta Grafica", "tarjeta grafica"),
    ("Discovery Grafica", "grafica"),
]

# GPU only — no other product categories tracked.

# Comps searches: uncapped and nationwide for the widest distribution. The
# variants get their own searches so their prices never contaminate the base
# model's median.
# Every model in SEED_PRICES needs one, otherwise it can never learn a real
# reference price and stays pinned to its seed forever. The high-end cards are
# included even though nothing near their market value could ever clear
# MAX_CAPITAL_PRICE: the whole point is that a 4090 listed at 340 EUR is the
# best possible outcome, and recognising that requires knowing what a 4090 is
# worth.
COMPS_MODELS = [
    ("rtx_3050", "rtx 3050"),
    ("rtx_3060", "rtx 3060"),
    ("rtx_3060_ti", "rtx 3060 ti"),
    ("rtx_3070", "rtx 3070"),
    ("rtx_3070_ti", "rtx 3070 ti"),
    ("rtx_3080", "rtx 3080"),
    ("rtx_3080_ti", "rtx 3080 ti"),
    ("rtx_3090", "rtx 3090"),
    ("rtx_4060", "rtx 4060"),
    ("rtx_4060_ti", "rtx 4060 ti"),
    ("rtx_4070", "rtx 4070"),
    ("rtx_4070_super", "rtx 4070 super"),
    ("rtx_4070_ti", "rtx 4070 ti"),
    ("rtx_4070_ti_super", "rtx 4070 ti super"),
    ("rtx_4080", "rtx 4080"),
    ("rtx_4080_super", "rtx 4080 super"),
    ("rtx_4090", "rtx 4090"),
    ("rtx_5060", "rtx 5060"),
    ("rtx_5060_ti", "rtx 5060 ti"),
    ("rtx_5070", "rtx 5070"),
    ("rtx_5070_ti", "rtx 5070 ti"),
    ("rtx_5080", "rtx 5080"),
    ("rtx_5090", "rtx 5090"),
    ("rx_6600", "rx 6600"),
    ("rx_6600_xt", "rx 6600 xt"),
    ("rx_6650_xt", "rx 6650 xt"),
    ("rx_6700_xt", "rx 6700 xt"),
    ("rx_6750_xt", "rx 6750 xt"),
    ("rx_6800", "rx 6800"),
    ("rx_6800_xt", "rx 6800 xt"),
    ("rx_7600", "rx 7600"),
    ("rx_7600_xt", "rx 7600 xt"),
    ("rx_7700_xt", "rx 7700 xt"),
    ("rx_7800_xt", "rx 7800 xt"),
    ("rx_7900_gre", "rx 7900 gre"),
    ("rx_7900_xt", "rx 7900 xt"),
    ("rx_7900_xtx", "rx 7900 xtx"),
    ("rx_9060_xt", "rx 9060 xt"),
    ("rx_9070", "rx 9070"),
    ("rx_9070_xt", "rx 9070 xt"),
]

# Rough current-market resale values in EUR, used only until each model has
# MIN_COMPS real comps. Adjust freely — they exist so day-one alerts aren't
# nonsense, not to be accurate forever.
SEED_PRICES = {
    "rtx_3050": 110,
    "rtx_3060": 150,
    "rtx_3060_12g": 165,
    "rtx_3060_ti": 185,
    "rtx_3070": 210,
    "rtx_3070_ti": 240,
    "rtx_3080": 290,
    "rtx_3080_ti": 350,
    "rtx_3090": 450,
    "rtx_4060": 220,
    "rtx_4060_ti": 280,
    "rtx_4060_ti_8g": 270,
    "rtx_4060_ti_16g": 320,
    "rtx_4070": 330,
    "rtx_4070_super": 400,
    "rtx_4070_ti": 450,
    "rtx_4070_ti_super": 520,
    "rtx_4080": 620,
    "rtx_4080_super": 650,
    "rtx_4090": 1200,
    "rtx_5060": 260,
    "rtx_5060_ti": 350,
    "rtx_5060_ti_8g": 320,
    "rtx_5060_ti_16g": 380,
    "rtx_5070": 480,
    "rtx_5070_ti": 650,
    # Every key in models.REGISTRY needs an entry. A model with an alert search
    # but no seed price falls through to the bootstrap-cap branch, which fires a
    # bare "matches your search" alert with no margin analysis at all — the
    # noisy behaviour the margin engine exists to replace. There is a test
    # pinning REGISTRY and SEED_PRICES to the same key set.
    "rtx_3080_12g": 310,
    "rtx_3090_ti": 520,
    "rtx_5080": 900,
    "rtx_5090": 1800,
    "rx_6600": 130,
    "rx_6600_xt": 150,
    "rx_6650_xt": 165,
    "rx_6700_xt": 200,
    "rx_6750_xt": 225,
    "rx_6800": 250,
    "rx_6800_xt": 290,
    "rx_7600": 200,
    "rx_7600_xt": 230,
    "rx_9060_xt_8g": 260,
    "rx_9060_xt_16g": 300,
    "rx_7700_xt": 320,
    "rx_7800_xt": 400,
    "rx_7900_gre": 480,
    "rx_7900_xt": 550,
    "rx_7900_xtx": 700,
    "rx_9060_xt": 280,
    "rx_9070": 480,
    "rx_9070_xt": 550,
}


# Server-side floor on every seeded search, alert and comps alike.
#
# No seeded search set one, so `min_sale_price` was never sent and every 40-item
# page arrived with sub-50 EUR listings on it — which pricing.sane() then threw
# away locally, after they had already cost their slot on the page. Given how
# hard the search shape works to win page depth (the whole time_filter
# investigation in config.py exists to get a page from 16 items to 40), that was
# free depth left unclaimed for nothing.
#
# MIN_SANE_PRICE is the right number rather than MIN_COMP_PRICE, because this
# bounds what the *API* returns, and nothing below the sanity band can be used
# by either loop: an alert can't fire on it and it can't enter the comps pool.
# A real GPU under 50 EUR is a dead card, a replica, a bare cooler or bait —
# never a flip and never a comp — so filtering it at the server costs nothing
# and buys back page slots for listings that can actually be acted on.
MIN_SEARCH_PRICE = config.MIN_SANE_PRICE


def build_search_rows() -> list[dict]:
    rows: list[dict] = []

    for label, keywords, model_key, cap in ALERT_SEARCHES:
        rows.append(
            {
                "label": label,
                "role": "alert",
                "keywords": keywords,
                "model_key": model_key,
                "category_ids": GPU,
                # Bootstrap-only fallback cap, used while the model has no
                # learned reference price yet. Not an API-side restriction —
                # searches run nationwide/international with no price cap.
                "max_price": cap,
                "min_price": MIN_SEARCH_PRICE,
                "distance_km": None,
                "active": True,
            }
        )

    for label, keywords in DISCOVERY_SEARCHES:
        rows.append(
            {
                "label": label,
                "role": "alert",
                "keywords": keywords,
                "model_key": None,
                "category_ids": GPU,
                "max_price": None,
                "min_price": MIN_SEARCH_PRICE,
                "distance_km": None,
                "active": True,
            }
        )

    for model_key, keywords in COMPS_MODELS:
        rows.append(
            {
                "label": f"Comps {keywords.upper()}",
                "role": "comps",
                "keywords": keywords,
                "model_key": model_key,
                "category_ids": GPU,
                # Still uncapped upward — the reference price needs the full
                # distribution — but floored, for the same page-depth reason as
                # the alert searches. A sub-50 EUR listing is barred from the
                # comps pool by MIN_COMP_PRICE anyway, so nothing that could
                # move a median is being filtered out here.
                "max_price": None,
                "min_price": MIN_SEARCH_PRICE,
                "distance_km": None,
                "active": True,
            }
        )

    return rows


def retire_orphaned_searches(db: Database, current_labels: set[str]) -> int:
    """Deactivate any search this file no longer defines. Returns the count.

    Searches upsert on `label`, which means this file can only ever *add* to the
    table. Anything defined by an earlier version of it stays active forever:
    the reverted phone searches are the obvious case, but so is every keyword
    that was tried once and dropped. Each one costs a request on every pass, and
    a stale comps row is worse than merely wasteful — `covered` in comps_loop is
    built from comps search rows, and it is the set of models allowed to have
    their listings judged absent and closed.

    Deactivated rather than deleted: `active=false` keeps the row (and its id,
    which nothing references but which is cheap to preserve) so a search can be
    turned back on from the dashboard, and so this is safe to run without
    thinking about what it might destroy.

    The comparison is done client-side and the update is keyed on integer ids
    rather than sending labels through a PostgREST `not.in.(...)` filter: labels
    contain spaces and are free text, and quoting them into a filter expression
    is a needless way to get this wrong.
    """
    res = db.c.table("searches").select("id,label,active").execute()
    orphan_ids = [
        row["id"]
        for row in (res.data or [])
        if row.get("active") and row["label"] not in current_labels
    ]
    if not orphan_ids:
        return 0
    for batch in chunked(orphan_ids, 100):
        db.c.table("searches").update({"active": False}).in_("id", list(batch)).execute()
    return len(orphan_ids)


def main() -> None:
    config.setup_logging()
    db = Database()

    rows = build_search_rows()
    db.c.table("searches").upsert(rows, on_conflict="label").execute()
    log.info("seeded %d searches", len(rows))
    retired = retire_orphaned_searches(db, {r["label"] for r in rows})
    if retired:
        log.info("deactivated %d search(es) this file no longer defines", retired)

    existing = db.get_model_prices()
    written = 0
    skipped = 0
    for model_key, ref in SEED_PRICES.items():
        current = existing.get(model_key)
        if current and not current.get("is_seed"):
            skipped += 1
            continue  # a learned price already beat this seed
        db.upsert_model_price(
            {
                "model_key": model_key,
                "ref_price": ref,
                "n_comps": 0,
                "buy_ceiling": round(config.SHIPPED.buy_ceiling(ref), 2),
                "buy_ceiling_in_person": round(config.IN_PERSON.buy_ceiling(ref), 2),
                "updated_at": now().isoformat(),
                "is_seed": True,
            }
        )
        written += 1
    log.info("seeded %d model prices (%d left alone — already learned)", written, skipped)


if __name__ == "__main__":
    main()
