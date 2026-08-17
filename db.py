"""Supabase persistence layer.

All state lives here so the loops themselves stay stateless — that's what makes
an ephemeral GitHub Actions runner (or a future Oracle VM) interchangeable.
"""

from __future__ import annotations

import logging
import re
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


def _num_eq(a: Any, b: Any) -> bool:
    """Numeric equality across the str/Decimal/float mix PostgREST returns."""
    if a is None or b is None:
        return a is None and b is None
    try:
        return abs(float(a) - float(b)) < 0.005
    except (TypeError, ValueError):
        return False


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
    # Columns this process has learned the live table does not have. Code and
    # schema deploy independently here: a push reaches the runner within
    # minutes while schema.sql is applied by hand, so for a while the code
    # writes columns that do not exist yet. Without this, that window is a hard
    # outage — PostgREST rejects the whole batch with PGRST204 and every
    # listing in the run is lost, rather than just the new field.
    _missing_columns: set[str] = set()

    @staticmethod
    def _unknown_column(exc: Exception) -> str | None:
        """The column name PostgREST is complaining about, if that's the error."""
        message = str(exc)
        if "PGRST204" not in message and "Could not find" not in message:
            return None
        match = re.search(r"'([^']+)' column", message) or re.search(
            r"Could not find the '([^']+)'", message
        )
        return match.group(1) if match else None

    def upsert_listings(self, rows: list[dict]) -> None:
        for batch in chunked(rows):
            payload = [
                {k: v for k, v in row.items() if k not in self._missing_columns}
                for row in batch
            ]
            while True:
                try:
                    self.c.table("listings").upsert(payload, on_conflict="item_id").execute()
                    break
                except Exception as exc:
                    column = self._unknown_column(exc)
                    if column is None or column in self._missing_columns:
                        raise
                    log.warning(
                        "listings has no column %r yet — dropping it and retrying. "
                        "Apply the ALTER statements in schema.sql to persist it.",
                        column,
                    )
                    self._missing_columns.add(column)
                    payload = [
                        {k: v for k, v in row.items() if k != column} for row in payload
                    ]

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

    def listing_states(self, item_ids: Sequence[str]) -> dict[str, tuple]:
        """(last_price, last_status) per listing, as it stands right now.

        Read *before* upsert_listings overwrites it, and passed back into
        insert_changed_observations afterwards. The two-step exists because the
        ordering is forced from both sides: the comparison needs the old state,
        but observations.item_id has a foreign key to listings, so the row has
        to exist before its observation can be written. Writing observations
        first crashed every production run with a 23503 the moment a listing
        appeared that we had never seen before.
        """
        return {
            item_id: (row.get("last_price"), row.get("last_status"))
            for item_id, row in self.get_listings(item_ids).items()
        }

    def insert_changed_observations(
        self, rows: list[dict], previous: dict[str, tuple] | None = None
    ) -> int:
        """Record only the observations that say something new.

        An observation exists to capture a *change* — a price cut, or a listing
        going reserved. Writing one every pass regardless meant a listing that
        sat untouched for three weeks produced ~6000 identical rows, and the
        comps pool then had to dedup them right back down to one price per item
        anyway. It is the same waste that made junk_exclusions 97% of the
        database, just slower.

        Skipping unchanged rows loses nothing downstream: collect_comps takes
        one price per item, and last_reserved_price takes the newest reserved
        row — both of which the change-only trail still answers exactly.
        """
        if not rows:
            return 0
        if previous is None:
            previous = self.listing_states([r["item_id"] for r in rows])
        fresh = []
        for row in rows:
            prior = previous.get(row["item_id"])
            if prior is not None:
                last_price, last_status = prior
                if _num_eq(last_price, row.get("price")) and last_status == row.get("status"):
                    continue
            fresh.append(row)
        self.insert_observations(fresh)
        return len(fresh)

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
        """Listings inferred sold (reserved, then gone) inside the window.

        `whole_machine` rows are excluded: a gaming laptop or a prebuilt that
        sold for 900 EUR is a real transaction, but it is not a transaction in
        the loose card its title names, and letting it into the pool moves the
        reference price by a whole tier.
        """
        res = (
            self.c.table("listings")
            .select("item_id,sold_price,closed_at,first_seen,posted_at")
            .eq("model_key", model_key)
            .eq("last_status", "closed")
            .not_.is_("sold_price", "null")
            .eq("whole_machine", False)
            .gte("closed_at", iso(since))
            .limit(PAGE)
            .execute()
        )
        return res.data or []

    def sold_durations(self, model_key: str, since: datetime) -> list[dict]:
        """Listed-at / closed-at pairs, for median days-to-sale.

        Prefers the seller's real `posted_at` over `first_seen`, which is only
        when this bot happened to notice the listing.
        """
        return self.sold_comps(model_key, since)

    def open_reserved_listings(self, model_keys: Sequence[str]) -> list[dict]:
        """Reserved listings we still believe are live, for direct liveness checks.

        These are the only ones worth spending a detail request on: a reserved
        listing that disappears is the single event that produces a sold comp.
        """
        if not model_keys:
            return []
        cutoff = iso(now() - timedelta(days=config.STALE_LISTING_DAYS))
        out: list[dict] = []
        for batch in chunked(model_keys, 50):
            res = (
                self.c.table("listings")
                .select("item_id,model_key,last_price,last_status,ever_reserved,missing_runs,title")
                .in_("model_key", list(batch))
                .in_("last_status", ["active", "reserved"])
                .eq("ever_reserved", True)
                .gte("last_seen", cutoff)
                .limit(PAGE)
                .execute()
            )
            out.extend(res.data or [])
        return out

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
        """Record why a listing was excluded, at most once per listing.

        This table exists to tune the filters, so one row per exclusion is all
        it was ever meant to hold. Plain inserts made it one row per exclusion
        *per run*: the same "Funda iPhone 15" was re-logged every five minutes
        for as long as it stayed listed, ~288 times a day. It reached 2.86M
        rows in 16 days — 97% of the database and the reason the free-tier
        quota ran out — while carrying only a few thousand distinct listings.

        Dedup is done by reading back which listings are already recorded
        rather than by an upsert, so it needs no unique index and works against
        the existing table immediately — the same shape as alerted_prices().
        """
        if not rows:
            return
        deduped = {r["item_id"]: r for r in rows if r.get("item_id")}
        if not deduped:
            return

        known: set[str] = set()
        for batch in chunked(list(deduped), 200):
            try:
                res = (
                    self.c.table("junk_exclusions")
                    .select("item_id")
                    .in_("item_id", list(batch))
                    .limit(PAGE)
                    .execute()
                )
                known.update(r["item_id"] for r in (res.data or []))
            except Exception as exc:
                # Better to risk a duplicate than to lose the audit trail.
                log.warning("junk dedup lookup failed: %s", exc)

        fresh = [row for iid, row in deduped.items() if iid not in known]
        if not fresh:
            return
        for batch in chunked(fresh):
            self.c.table("junk_exclusions").insert(list(batch)).execute()

    def purge_old_junk(self) -> None:
        """Junk rows past the retention horizon.

        Even deduplicated this grows with every new listing the filters reject,
        and nothing reads a months-old exclusion — the phrase lists get tuned
        against what the filters are catching now.
        """
        self._purge_before(
            "junk_exclusions", now() - timedelta(days=config.JUNK_RETENTION_DAYS)
        )

    def _purge_before(self, table: str, cutoff: datetime, *, slice_hours: int = 6) -> None:
        """Delete `table` rows older than `cutoff`, in time slices.

        A single `delete where seen_at < cutoff` is the obvious implementation
        and it does not survive contact with a real backlog: Postgres cancels
        it on the statement timeout (57014) and *nothing* gets deleted, so the
        table only grows and every subsequent run fails the same way. Slicing
        keeps each statement small enough to commit, and bounding the number of
        slices per run stops housekeeping from monopolising a cycle — a
        backlog then drains over several runs instead of never.
        """
        oldest = self._oldest(table)
        if oldest is None:
            return
        window = timedelta(hours=slice_hours)
        start = oldest
        for _ in range(200):  # bounded work per run
            if start >= cutoff:
                return
            end = min(start + window, cutoff)
            try:
                self.c.table(table).delete().lt("seen_at", iso(end)).gte(
                    "seen_at", iso(start)
                ).execute()
            except Exception as exc:
                log.warning("%s purge slice %s failed: %s", table, start, exc)
                return
            start = end

    def _oldest(self, table: str) -> datetime | None:
        try:
            res = (
                self.c.table(table).select("seen_at").order("seen_at").limit(1).execute()
            )
            rows = res.data or []
            if not rows:
                return None
            return datetime.fromisoformat(str(rows[0]["seen_at"]).replace("Z", "+00:00"))
        except Exception as exc:
            log.warning("%s oldest-row lookup failed: %s", table, exc)
            return None

    def purge_old_observations(self) -> None:
        """Keep `observations` inside the free-tier storage budget.

        One row per listing per run at a 5-minute cadence is ~100k rows/day,
        which fills a free Supabase project in weeks. Nothing older than the
        comps window is ever read, so anything past the retention horizon is
        pure cost.
        """
        self._purge_before(
            "observations", now() - timedelta(days=config.OBSERVATION_RETENTION_DAYS)
        )

    def recent_runs(self, loop_name: str, limit: int) -> list[dict]:
        """Last N finished runs, newest first — input to the dead-man switch."""
        try:
            res = (
                self.c.table("run_log")
                .select("id,items_seen,alerts_sent,errors,started_at,finished_at")
                .eq("loop_name", loop_name)
                .not_.is_("finished_at", "null")
                .order("started_at", desc=True)
                .limit(limit)
                .execute()
            )
            return res.data or []
        except Exception as exc:
            log.warning("run history read failed: %s", exc)
            return []

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
