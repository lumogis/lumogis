# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""LUM-583 — list_my_shared_items: owner-scoped, projection-row read, resilient.

The fake here is **column-aware**: each table's rows are stored under their
REAL column names, and the fake projects exactly the columns the service's
SELECT/ORDER BY name — so a wrong ``label_col``/``shared_ts_col`` in the
registry (the feature's stated highest risk) makes the arm raise, the item
vanishes, and the test fails. It also records the emitted SQL so the owner
predicate can be asserted directly (not just modelled in the fake).
"""

from __future__ import annotations

import re
from datetime import datetime
from datetime import timezone

import pytest
from services.sharing_registry import SHAREABLE_RESOURCES

import config
from services import shared_items as svc

# The REAL columns each shareable table exposes (verified against
# postgres/init.sql + migration 003). Seeding rows under these names lets the
# fake reproduce Postgres's "column does not exist" if the registry ever pins a
# column a table lacks (e.g. file_index has no created_at).
_REAL_COLUMNS = {
    "notes": {"published_from", "user_id", "text", "created_at", "updated_at"},
    "audio_memos": {"published_from", "user_id", "transcript", "created_at", "updated_at"},
    "sessions": {"published_from", "user_id", "summary", "created_at", "updated_at"},
    "file_index": {"published_from", "user_id", "file_path", "ingested_at", "updated_at"},
    "entities": {"published_from", "user_id", "name", "created_at", "updated_at"},
    "signals": {"published_from", "user_id", "title", "created_at", "published_at"},
}

_SELECT_RE = re.compile(
    r"select published_from, (\w+) as label, (\w+) as shared_at from (\w+) "
    r"where scope = 'shared' and user_id = %s and published_from is not null "
    r"order by (\w+) desc"
)


class _ColumnAwareStore:
    """Faithful-ish store: projects only the columns the query names and raises
    on a column the table doesn't have (models Postgres column errors)."""

    def __init__(self, rows_by_table, fail_tables=()):
        self.rows_by_table = rows_by_table
        self.fail_tables = set(fail_tables)
        self.queries: list[str] = []

    def fetch_all(self, query, params=None):
        norm = " ".join(query.split()).lower()
        self.queries.append(norm)
        m = _SELECT_RE.search(norm)
        assert m, f"query shape changed: {norm}"
        label_col, ts_col, table, order_col = m.group(1), m.group(2), m.group(3), m.group(4)
        assert order_col == ts_col  # ORDER BY must use the timestamp column
        if table in self.fail_tables:
            raise RuntimeError("simulated query failure")
        real = _REAL_COLUMNS[table]
        if label_col not in real:
            raise RuntimeError(f'column "{label_col}" does not exist on {table}')
        if ts_col not in real:
            raise RuntimeError(f'column "{ts_col}" does not exist on {table}')
        user_id = params[0]
        out = []
        for r in self.rows_by_table.get(table, []):
            if r["user_id"] != user_id:
                continue
            out.append(
                {
                    "published_from": r["published_from"],
                    "label": r.get(label_col),
                    "shared_at": r.get(ts_col),
                }
            )
        return out


def _install(monkeypatch, store):
    monkeypatch.setattr(config, "get_metadata_store", lambda: store)


def _one_row_per_type(owner="alice"):
    """A shared row for every one of the six types, keyed by real columns."""
    return {
        "notes": [
            {"published_from": "n-1", "user_id": owner, "text": "grocery list", "created_at": None}
        ],
        "audio_memos": [
            {"published_from": "a-1", "user_id": owner, "transcript": "memo", "created_at": None}
        ],
        "sessions": [
            {"published_from": "s-1", "user_id": owner, "summary": "chat", "created_at": None}
        ],
        "file_index": [
            {"published_from": 7, "user_id": owner, "file_path": "tax.pdf", "updated_at": None}
        ],
        "entities": [
            {"published_from": "e-1", "user_id": owner, "name": "Acme", "created_at": None}
        ],
        "signals": [
            {"published_from": "g-1", "user_id": owner, "title": "alert", "created_at": None}
        ],
    }


def test_registry_columns_are_valid_for_all_six_types(monkeypatch):
    """Every arm's label_col + shared_ts_col must be a real column on its table —
    a wrong pin (e.g. file_index→created_at) makes the arm raise and the item
    vanish, which this catches (the fake mirrors Postgres column errors)."""
    store = _ColumnAwareStore(_one_row_per_type())
    _install(monkeypatch, store)
    items = svc.list_my_shared_items("alice")
    got = {i["resource_type"] for i in items}
    assert got == set(SHAREABLE_RESOURCES)  # all six present → all columns valid
    assert len(items) == 6


def test_each_arm_binds_owner_and_shared_scope(monkeypatch):
    """The privacy predicate lives in the SQL, not just the fake: every arm's
    query must filter scope='shared' AND user_id = %s."""
    store = _ColumnAwareStore(_one_row_per_type())
    _install(monkeypatch, store)
    svc.list_my_shared_items("alice")
    assert len(store.queries) == len(SHAREABLE_RESOURCES)
    for q in store.queries:
        assert "scope = 'shared'" in q
        assert "user_id = %s" in q


def test_resource_id_is_source_pk_not_projection_pk(monkeypatch):
    src = "source-note-uuid"
    store = _ColumnAwareStore(
        {"notes": [{"published_from": src, "user_id": "alice", "text": "n", "created_at": None}]}
    )
    _install(monkeypatch, store)
    items = svc.list_my_shared_items("alice")
    assert items[0]["resource_type"] == "notes"
    assert items[0]["resource_id"] == src  # the SOURCE pk (unshare-route-compatible)


def test_files_resource_id_stringified(monkeypatch):
    store = _ColumnAwareStore(
        {
            "file_index": [
                {"published_from": 7, "user_id": "alice", "file_path": "x.pdf", "updated_at": None}
            ]
        }
    )
    _install(monkeypatch, store)
    items = svc.list_my_shared_items("alice")
    assert items[0]["resource_id"] == "7"


def test_isolation_never_shows_another_members_items(monkeypatch):
    """The privacy merge-gate: member B's shared items never appear for A."""
    store = _ColumnAwareStore(
        {
            "file_index": [
                {"published_from": 1, "user_id": "alice", "file_path": "a.pdf", "updated_at": None},
                {"published_from": 2, "user_id": "bob", "file_path": "b.pdf", "updated_at": None},
            ]
        }
    )
    _install(monkeypatch, store)
    assert [i["resource_id"] for i in svc.list_my_shared_items("alice")] == ["1"]
    assert [i["resource_id"] for i in svc.list_my_shared_items("bob")] == ["2"]


def test_empty_when_nothing_shared(monkeypatch):
    _install(monkeypatch, _ColumnAwareStore({}))
    assert svc.list_my_shared_items("alice") == []


def test_partial_arm_failure_degrades(monkeypatch):
    store = _ColumnAwareStore(_one_row_per_type(), fail_tables={"entities"})
    _install(monkeypatch, store)
    items = svc.list_my_shared_items("alice")
    types = {i["resource_type"] for i in items}
    assert "entities" not in types  # failed arm isolated
    assert len(items) == 5  # the other five still returned


def test_all_arms_failing_raises_not_empty(monkeypatch):
    """A total store outage must not masquerade as 'nothing shared'."""
    store = _ColumnAwareStore(_one_row_per_type(), fail_tables=set(_REAL_COLUMNS))
    _install(monkeypatch, store)
    with pytest.raises(RuntimeError):
        svc.list_my_shared_items("alice")


def test_global_recency_sort_across_types(monkeypatch):
    def ts(day):
        return datetime(2026, 6, day, tzinfo=timezone.utc)

    store = _ColumnAwareStore(
        {
            "notes": [
                {"published_from": "n-old", "user_id": "alice", "text": "n", "created_at": ts(1)}
            ],
            "file_index": [
                {
                    "published_from": 9,
                    "user_id": "alice",
                    "file_path": "f.pdf",
                    "updated_at": ts(20),
                }
            ],
            "entities": [
                {"published_from": "e-mid", "user_id": "alice", "name": "E", "created_at": ts(10)}
            ],
        }
    )
    _install(monkeypatch, store)
    items = svc.list_my_shared_items("alice")
    # Most recently shared first, across types (file day-20, entity day-10, note day-1).
    assert [i["resource_id"] for i in items] == ["9", "e-mid", "n-old"]


def test_label_truncated(monkeypatch):
    store = _ColumnAwareStore(
        {
            "notes": [
                {"published_from": "n-1", "user_id": "alice", "text": "x" * 300, "created_at": None}
            ]
        }
    )
    _install(monkeypatch, store)
    items = svc.list_my_shared_items("alice")
    assert len(items[0]["label"]) <= 120
