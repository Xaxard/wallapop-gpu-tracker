"""Every column the code writes must exist in schema.sql.

This is the test that would have caught `listings.country`. The column was
parsed by wallapop_client, written by alert_loop, asserted on by a parser test —
and never created. Every fresh process therefore sent one batch that failed with
PGRST204, logged the "apply the ALTER statements" warning, dropped the field and
carried on, so the failure was invisible in the loop's own output and the value
was never once persisted. The loop tests could not catch it either: their FakeDB
accepts any key, because that is what makes it a useful wiring fake.

So this test reads the real schema.sql — both the `create table` bodies and the
idempotent `alter table ... add column` statements — and checks the row shapes
the code actually writes against it. Two rules keep it honest:

  * it introspects the row builders rather than restating their keys, so a new
    field is covered the moment it is written;
  * every discovery step is itself asserted, so a renamed builder or a
    refactored call site fails loudly instead of quietly checking nothing.

Nothing here touches the network or a database.
"""

from __future__ import annotations

import ast
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import alert_loop  # noqa: E402
import comps_loop  # noqa: E402
import models  # noqa: E402
import pricing  # noqa: E402
from db import iso, now  # noqa: E402
from wallapop_client import Item  # noqa: E402

SCHEMA_PATH = ROOT / "schema.sql"
SCHEMA_SQL = SCHEMA_PATH.read_text()


# ------------------------------------------------------------ schema parsing
_CREATE_RE = re.compile(
    r"create\s+table\s+(?:if\s+not\s+exists\s+)?(\w+)\s*\((.*?)\n\s*\)\s*;",
    re.IGNORECASE | re.DOTALL,
)
_ALTER_RE = re.compile(
    r"alter\s+table\s+(\w+)\s+add\s+column\s+(?:if\s+not\s+exists\s+)?(\w+)",
    re.IGNORECASE,
)
_INDEX_RE = re.compile(
    r"create\s+(?:unique\s+)?index\s+(?:if\s+not\s+exists\s+)?(\w+)\s+on\s+(\w+)",
    re.IGNORECASE,
)

# Table-level constraint clauses live in the same comma-separated list as the
# columns; their first word is never a column name.
_CONSTRAINT_WORDS = {
    "unique",
    "primary",
    "check",
    "foreign",
    "constraint",
    "exclude",
    "like",
}


def _strip_comments(sql: str) -> str:
    return "\n".join(line.split("--")[0] for line in sql.splitlines())


def _split_top_level(body: str) -> list[str]:
    """Split a create-table body on commas outside parentheses.

    `check (role in ('alert','comps'))` contains a comma that is not a column
    separator, so a plain body.split(",") mis-parses the searches table.
    """
    parts: list[str] = []
    depth = 0
    current: list[str] = []
    for char in body:
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        if char == "," and depth == 0:
            parts.append("".join(current))
            current = []
            continue
        current.append(char)
    parts.append("".join(current))
    return parts


def parse_schema(sql: str) -> tuple[dict[str, set[str]], dict[str, str]]:
    """(table -> columns, index name -> table) as schema.sql declares them."""
    clean = _strip_comments(sql)
    tables: dict[str, set[str]] = {}
    for table, body in _CREATE_RE.findall(clean):
        columns: set[str] = set()
        for part in _split_top_level(body):
            words = part.strip().split()
            if not words or words[0].lower() in _CONSTRAINT_WORDS:
                continue
            columns.add(words[0])
        tables[table] = columns
    for table, column in _ALTER_RE.findall(clean):
        tables.setdefault(table, set()).add(column)
    indexes = {name: table for name, table in _INDEX_RE.findall(clean)}
    return tables, indexes


SCHEMA, INDEXES = parse_schema(SCHEMA_SQL)


def _explain(table: str, columns: set[str]) -> str:
    missing = sorted(columns - SCHEMA.get(table, set()))
    return (
        f"{table} is missing {missing} — the code writes {'them' if len(missing) > 1 else 'it'} "
        f"but schema.sql never creates {'them' if len(missing) > 1 else 'it'}. Add "
        + "; ".join(
            f"alter table {table} add column if not exists {c} <type>" for c in missing
        )
        + " to schema.sql (with a comment saying why the column exists), and apply "
        "it in the Supabase SQL editor. Until then PostgREST rejects the batch "
        "with PGRST204 and db.Database silently drops the field."
    )


def assert_columns_exist(table: str, columns: set[str]) -> None:
    assert table in SCHEMA, f"schema.sql declares no table named {table!r}"
    assert columns <= SCHEMA[table], _explain(table, columns)


# ------------------------------------------------------- parser self-checks
# Without these the whole file could pass by parsing nothing at all.
def test_parser_finds_every_table():
    assert {
        "searches",
        "listings",
        "observations",
        "model_prices",
        "sent_alerts",
        "junk_exclusions",
        "run_log",
    } <= set(SCHEMA)


def test_parser_reads_columns_out_of_create_bodies_and_alters():
    # from the create body, including one whose definition carries a check(...)
    assert {"item_id", "title", "model_key", "confidence"} <= SCHEMA["listings"]
    assert {"id", "label", "role", "keywords"} <= SCHEMA["searches"]
    # from the idempotent ALTERs further down the file
    assert {"condition", "brand", "taxonomy", "whole_machine"} <= SCHEMA["listings"]
    # and no constraint clause was mistaken for a column
    assert not {"unique", "primary", "check"} & SCHEMA["searches"]


# ------------------------------------------------- the cross-agent contracts
@pytest.mark.parametrize(
    "table,column",
    [
        ("listings", "country"),       # D1: written since the nationwide switch
        ("listings", "seller_id"),     # A6: the only id that survives a relisting
        ("listings", "modified_at"),   # D4: the "seller just cut the price" signal
        ("model_prices", "n_own"),     # B6: own comps vs comps borrowed from a sibling
    ],
)
def test_contract_column_exists(table, column):
    assert column in SCHEMA[table], _explain(table, {column})


def test_observations_has_a_plain_seen_at_index():
    """C3. Retention slices by seen_at, and neither existing index can serve
    `order by seen_at limit 1` or a bare seen_at range delete — a leading
    model_key/item_id column is useless for both. On the biggest table in the
    database that is the statement timeout the slicing exists to avoid."""
    assert INDEXES.get("observations_seen_at_idx") == "observations", (
        "add `create index if not exists observations_seen_at_idx on "
        "observations (seen_at);` to schema.sql"
    )


def test_whole_machine_is_backfilled_and_constrained():
    """B4. The query side is NULL-tolerant now, but a column whose meaning
    depends on every reader remembering that is a trap, so the invariant is made
    true at the storage layer as well."""
    clean = _strip_comments(SCHEMA_SQL).lower()
    assert "update listings set whole_machine = false where whole_machine is null" in clean
    assert "alter column whole_machine set default false" in clean
    assert "alter column whole_machine set not null" in clean


# --------------------------------------------------------- the row builders
FULL_ITEM = Item(
    item_id="contract-1",
    title="RTX 3070 Gigabyte OC",
    description="Como nueva, con caja",
    price=250.0,
    currency="EUR",
    web_url="https://es.wallapop.com/item/contract-1",
    image_url="https://cdn/x.jpg",
    reserved=True,
    shipping=True,
    location="Madrid",
    distance_km=4.0,
    country="ES",
    seller_id="user-abc-123",
    condition="good",
    brand="NVIDIA",
    taxonomy=(24200, 10304),
    posted_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
    modified_at=datetime(2026, 8, 2, tzinfo=timezone.utc),
    user_allows_shipping=True,
)
FULL_MATCH = models.Match("rtx_3070", "RTX 3070", "high", vram=8)

def _discover_row_builders() -> list[tuple[str, object]]:
    """Every `*listing_row` callable the loops expose, deduplicated.

    Discovered rather than hard-coded because the builder has already moved
    once: it used to exist twice, once per loop, and the two copies drifted —
    only the alert loop's wrote `country`. It is now one function in alert_loop
    that both loops import, with `_listing_row` kept as an alias. Matching on
    the name means the check follows a rename or a re-split instead of quietly
    testing an obsolete copy, while a builder that disappears entirely still
    trips the guard test below.
    """
    found: dict[object, str] = {}
    for name, module in (("alert_loop", alert_loop), ("comps_loop", comps_loop)):
        for attr, value in sorted(vars(module).items()):
            if attr.endswith("listing_row") and callable(value):
                found.setdefault(value, f"{name}.{attr}")
    return [(label, fn) for fn, label in found.items()]


ROW_BUILDERS = _discover_row_builders()


def test_a_listings_row_builder_was_found_at_all():
    """If the builders are renamed out of this pattern, fail loudly rather than
    parametrize over an empty list and report green — silently checking nothing
    is the exact failure mode that let `country` go missing for months."""
    assert ROW_BUILDERS, (
        "no `*listing_row` callable found in alert_loop or comps_loop. This test "
        "introspects it to learn which columns the loops write to `listings`; "
        "update _discover_row_builders() to point at its replacement rather than "
        "dropping the check."
    )


@pytest.mark.parametrize("label,builder", ROW_BUILDERS)
def test_the_row_builder_returns_a_listings_row(label, builder):
    row = builder(FULL_ITEM, FULL_MATCH)
    assert isinstance(row, dict) and row.get("item_id") == FULL_ITEM.item_id


@pytest.mark.parametrize("label,builder", ROW_BUILDERS)
def test_listing_row_only_names_columns_that_exist(label, builder):
    """A fully-populated Item, so no field is skipped by an `if x else None`."""
    assert_columns_exist("listings", set(builder(FULL_ITEM, FULL_MATCH)))


@pytest.mark.parametrize("label,builder", ROW_BUILDERS)
@pytest.mark.parametrize("column,expected", [("country", "ES")])
def test_listing_row_actually_carries_the_new_fields(label, builder, column, expected):
    """Guards the test above from passing on a row that simply omits them: an
    unwritten column is not a schema problem, it is a silently lost signal, and
    it is how `country` managed to be parsed, tested and written yet never
    stored."""
    assert builder(FULL_ITEM, FULL_MATCH)[column] == expected


# ------------------------------------------- rows written straight from the loops
# Method -> the table it writes. Row dicts built inline at the call site (the
# observation rows, the junk rows) never pass through a named builder, so they
# are harvested from the source instead.
DB_WRITERS = {
    "upsert_listings": "listings",
    "insert_observations": "observations",
    "insert_changed_observations": "observations",
    "log_junk": "junk_exclusions",
    "record_alert": "sent_alerts",
    "upsert_model_price": "model_prices",
}


def _dict_keys_in(node: ast.AST) -> set[str]:
    """String keys of every dict literal in this subtree."""
    keys: set[str] = set()
    for inner in ast.walk(node):
        if isinstance(inner, ast.Dict):
            for key in inner.keys:
                if isinstance(key, ast.Constant) and isinstance(key.value, str):
                    keys.add(key.value)
    return keys


def _keys_for_name(tree: ast.AST, name: str, depth: int = 0) -> set[str]:
    """Keys of the dict rows a local variable accumulates.

    Covers the three shapes the loops actually use: `rows = [ {...} for .. ]`,
    `rows.append({...})`, and `row = _listing_row(..)` followed by
    `row["extra"] = ..` before it is appended.
    """
    keys: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None:
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(isinstance(t, ast.Name) and t.id == name for t in targets):
                keys |= _dict_keys_in(node.value)
            for target in targets:
                if (
                    isinstance(target, ast.Subscript)
                    and isinstance(target.value, ast.Name)
                    and target.value.id == name
                    and isinstance(target.slice, ast.Constant)
                    and isinstance(target.slice.value, str)
                ):
                    keys.add(target.slice.value)
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in ("append", "extend")
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == name
        ):
            for arg in node.args:
                keys |= _dict_keys_in(arg)
                if isinstance(arg, ast.Name) and depth < 3:
                    keys |= _keys_for_name(tree, arg.id, depth + 1)
    return keys


def written_columns(module_path: Path) -> dict[str, set[str]]:
    """table -> every column name this module hands to a db writer."""
    tree = ast.parse(module_path.read_text())
    out: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        table = DB_WRITERS.get(node.func.attr)
        if table is None:
            continue
        keys: set[str] = set()
        for arg in list(node.args) + [kw.value for kw in node.keywords]:
            keys |= _dict_keys_in(arg)
            if isinstance(arg, ast.Name):
                keys |= _keys_for_name(tree, arg.id)
        out.setdefault(table, set()).update(keys)
    return out


LOOP_WRITES = {
    "alert_loop": written_columns(ROOT / "alert_loop.py"),
    "comps_loop": written_columns(ROOT / "comps_loop.py"),
}


@pytest.mark.parametrize("loop", sorted(LOOP_WRITES))
def test_the_source_scan_found_the_writes_it_is_meant_to_check(loop):
    """The scan is only as good as its ability to find the call sites. If a loop
    stops calling db.insert_changed_observations / db.log_junk under those names,
    fail here rather than silently check an empty set."""
    found = LOOP_WRITES[loop]
    assert "observations" in found and {"item_id", "model_key", "price", "status"} <= found["observations"]
    assert "junk_exclusions" in found and {"item_id", "phrase", "category"} <= found["junk_exclusions"]


@pytest.mark.parametrize("loop", sorted(LOOP_WRITES))
def test_loop_writes_only_name_columns_that_exist(loop):
    for table, columns in sorted(LOOP_WRITES[loop].items()):
        assert_columns_exist(table, columns)


# ------------------------------------------------------------- model_prices
class _PricingStub:
    """Just enough Database for recompute_model_price to produce its row."""

    def reserved_comps(self, model_key, since):
        return [
            {"item_id": f"r{i}", "price": 300.0 + i, "seen_at": iso(now())}
            for i in range(8)
        ]

    def sold_comps(self, model_key, since):
        return []

    def get_model_prices(self):
        return {}

    def upsert_model_price(self, row):
        self.row = row


def test_model_price_row_only_names_columns_that_exist():
    """C4's other half: the row pricing writes carries six ALTER-added columns,
    and a missing one now degrades instead of crashing — which makes it that much
    easier for the schema to drift unnoticed. So it is checked here too."""
    stub = _PricingStub()
    written = pricing.recompute_model_price(stub, "rtx_3070", None, sold_rows=[])

    assert written, (
        "pricing.recompute_model_price wrote no row for a model with 8 comps — "
        "this test can no longer see the model_prices contract; fix the stub."
    )
    assert_columns_exist("model_prices", set(written))
    assert set(getattr(stub, "row", {})) == set(written)
