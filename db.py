"""Supabase persistence layer.

All state lives here so the loops themselves stay stateless — that's what makes
an ephemeral GitHub Actions runner (or a future Oracle VM) interchangeable.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Sequence

from supabase import Client, create_client

import config

log = logging.getLogger("db")

# PostgREST caps a default select at 1000 rows; ask explicitly for more where
# a model could plausibly have a bigger history.
PAGE = 1000


def now() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def chunked(seq: Sequence[Any], size: int = 500) -> Iterable[Sequence[Any]]:
    for i in range(0, len(seq), size):
        yield seq[i : i + size]


class Database:
    def __init__(self, client: Client | None = None) -> None:
        if client is not None:
            self.c = client
        else:
            config.require_secrets("SUPABASE_URL", "SUPABASE_KEY")
            self.c = create_client(config.SUPABASE_URL, config.SUPABASE_KEY)

    # ------------------------------------------------------------- searches
    def get_searches(self, role: str) -> list[dict]:
        res = (
            self.c.table("searches")
            .select("*")
            .eq("role", role)
            .eq("active", True)
            .order("id")
            .execute()
        )
        return res.data or []

    # ------------------------------------------------------------- listings
    def upsert_listings(self, rows: list[dict]) -> None:
        for batch in chunked(rows):
            self.c.table("listings").upsert(list(batch), on_conflict="item_id").execute()

    def get_listings(self, item_ids: Sequence[str]) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for batch in chunked(item_ids, 200):
            res = self.c.table("listings").select("*").in_("item_id", list(batch)).execute()
            for row in res.data or []:
                out[row["item_id"]] = row
        return out

    def get_open_listings_for_models(self, model_keys: Sequence[str]) -> list[dict]:
        """Listings for these models that we still believe are on sale."""
        if not model_keys:
            return []
        cutoff = iso(now() - timedelta(days=config.STALE_LISTING_DAYS))
        out: list[dict] = []
        for batch in chunked(model_keys, 50):
            res = (
                self.c.table("listings")
                .select("item_id,model_key,last_price,last_status,ever_reserved,"
                        "missing_runs,last_seen,title")
                .in_("model_key", list(batch))
                .in_("last_status", ["active", "reserved"])
                .gte("last_seen", cutoff)
                .limit(PAGE)
                .execute()
            )
            out.extend(res.data or [])
        return out

    # --------------------------------------------------------- observations
    def insert_observations(self, rows: list[dict]) -> None:
        for batch in chunked(rows):
            self.c.table("observations").insert(list(batch)).execute()

    def reserved_comps(self, model_key: str, since: datetime) -> list[dict]:
        """Reserved observations for a model inside the trailing window."""
        res = (
            self.c.table("observations")
            .select("item_id,price,seen_at")
            .eq("model_key", model_key)
            .eq("status", "reserved")
            .gte("seen_at", iso(since))
            .order("seen_at", desc=True)
            .limit(PAGE)
            .execute()
        )
        return res.data or []

    def sold_comps(self, model_key: str, since: datetime) -> list[dict]:
        """Listings inferred sold (reserved, then gone) inside the window."""
        res = (
            self.c.table("listings")
            .select("item_id,sold_price,closed_at")
            .eq("model_key", model_key)
            .eq("last_status", "closed")
            .not_.is_("sold_price", "null")
            .gte("closed_at", iso(since))
            .limit(PAGE)
            .execute()
        )
        return res.data or []

    def last_reserved_price(self, item_id: str) -> float | None:
        """Price the item carried the last time we saw it reserved."""
        res = (
            self.c.table("observations")
            .select("price")
            .eq("item_id", item_id)
            .eq("status", "reserved")
            .order("seen_at", desc=True)
            .limit(1)
            .execute()
        )
        rows = res.data or []
        price = rows[0]["price"] if rows else None
        return float(price) if price is not None else None

    def mark_closed(self, item_id: str, sold_price: float | None) -> None:
        self.c.table("listings").update(
            {
                "last_status": "closed",
                "closed_at": iso(now()),
                "sold_price": sold_price,
            }
        ).eq("item_id", item_id).execute()

    def set_missing_runs(self, item_id: str, value: int) -> None:
        self.c.table("listings").update({"missing_runs": value}).eq(
            "item_id", item_id
        ).execute()

    # ---------------------------------------------------------- model prices
    def get_model_prices(self) -> dict[str, dict]:
        res = self.c.table("model_prices").select("*").limit(PAGE).execute()
        return {row["model_key"]: row for row in (res.data or [])}

    def upsert_model_price(self, row: dict) -> None:
        self.c.table("model_prices").upsert(row, on_conflict="model_key").execute()

    # ----------------------------------------------------------- sent_alerts
    def alerted_prices(self, item_ids: Sequence[str]) -> dict[str, list[float]]:
        """Every price we've already alerted, per item."""
        out: dict[str, list[float]] = {}
        for batch in chunked(item_ids, 200):
            res = (
                self.c.table("sent_alerts")
                .select("item_id,price")
                .in_("item_id", list(batch))
                .limit(PAGE)
                .execute()
            )
            for row in res.data or []:
                out.setdefault(row["item_id"], []).append(float(row["price"]))
        return out

    def record_alert(self, item_id: str, price: float, kind: str) -> None:
        # The (item_id, price) unique constraint is the real dedup guarantee;
        # ignore_duplicates makes a concurrent double-run a no-op rather than a
        # crash.
        self.c.table("sent_alerts").upsert(
            {"item_id": item_id, "price": price, "kind": kind},
            on_conflict="item_id,price",
            ignore_duplicates=True,
        ).execute()

    # ------------------------------------------------------------- auditing
    def log_junk(self, rows: list[dict]) -> None:
        if not rows:
            return
        for batch in chunked(rows):
            self.c.table("junk_exclusions").insert(list(batch)).execute()

    def start_run(self, loop_name: str) -> int | None:
        try:
            res = self.c.table("run_log").insert({"loop_name": loop_name}).execute()
            return (res.data or [{}])[0].get("id")
        except Exception as exc:  # run logging must never break a run
            log.warning("run_log insert failed: %s", exc)
            return None

    def finish_run(self, run_id: int | None, **fields: Any) -> None:
        if run_id is None:
            return
        try:
            self.c.table("run_log").update({"finished_at": iso(now()), **fields}).eq(
                "id", run_id
            ).execute()
        except Exception as exc:
            log.warning("run_log update failed: %s", exc)
