"""Idempotent seeding of `searches` and the day-one `model_prices`.

Run once after applying schema.sql:  python seed.py
Safe to re-run — searches upsert on their label, and seed prices never
overwrite a reference the comps loop has already learned.
"""

from __future__ import annotations

import logging

import config
from db import Database, now

log = logging.getLogger("seed")

GPU = config.CATEGORY_GPU
RADIUS = config.DEFAULT_DISTANCE_KM

# ---------------------------------------------------------------- §7 config
# Deal-alert searches. max_price is only a coarse API-side filter to keep the
# volume down; once a model has >= MIN_COMPS the learned buy_ceiling is what
# actually decides.
ALERT_SEARCHES = [
    ("RTX 3070", "rtx 3070", "rtx_3070", 200),
    ("RTX 3080", "rtx 3080", "rtx_3080", 250),
    ("RTX 4060", "rtx 4060", "rtx_4060", 200),
    ("RTX 4070", "rtx 4070", "rtx_4070", 400),
    ("RTX 5060", "rtx 5060", "rtx_5060", 250),
    ("RX 9060 XT", "9060 xt", "rx_9060_xt", 350),
]

# Broad discovery: no bootstrap cap, so these only ever fire when the margin
# engine has a confident reference price and the listing clears the ceiling.
DISCOVERY_SEARCHES = [
    ("Discovery RTX", "rtx"),
    ("Discovery RX", "rx"),
    ("Discovery AMD", "amd"),
]

# GPU only — no other product categories tracked.

# Comps searches: uncapped and nationwide for the widest distribution. The
# variants get their own searches so their prices never contaminate the base
# model's median.
COMPS_MODELS = [
    ("rtx_3070", "rtx 3070"),
    ("rtx_3070_ti", "rtx 3070 ti"),
    ("rtx_3080", "rtx 3080"),
    ("rtx_3080_ti", "rtx 3080 ti"),
    ("rtx_4060", "rtx 4060"),
    ("rtx_4060_ti", "rtx 4060 ti"),
    ("rtx_4070", "rtx 4070"),
    ("rtx_4070_super", "rtx 4070 super"),
    ("rtx_4070_ti", "rtx 4070 ti"),
    ("rtx_4070_ti_super", "rtx 4070 ti super"),
    ("rtx_5060", "rtx 5060"),
    ("rtx_5060_ti", "rtx 5060 ti"),
    ("rx_9060_xt", "rx 9060 xt"),
    ("rx_7600", "rx 7600"),
    ("rx_7700_xt", "rx 7700 xt"),
    ("rx_7800_xt", "rx 7800 xt"),
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
    "rx_7600": 200,
    "rx_7700_xt": 320,
    "rx_7800_xt": 400,
    "rx_7900_gre": 480,
    "rx_7900_xt": 550,
    "rx_7900_xtx": 700,
    "rx_9060_xt": 280,
    "rx_9070": 480,
    "rx_9070_xt": 550,
}


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
                "max_price": None,
                "distance_km": None,
                "active": True,
            }
        )

    return rows


def main() -> None:
    config.setup_logging()
    db = Database()

    rows = build_search_rows()
    db.c.table("searches").upsert(rows, on_conflict="label").execute()
    log.info("seeded %d searches", len(rows))

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
