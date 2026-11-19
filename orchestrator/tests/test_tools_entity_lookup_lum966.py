# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""The chat entity tool after a delete or a Hide (LUM-966).

A Qdrant point is a hint, not the truth: a point left behind by a delete (or for
an entry the member hid) must not answer with a name that no longer exists.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

import config as _config
from services import tools

EID = "11111111-1111-4111-9111-111111111111"
OWNER = "99999999-9999-4999-9999-999999999999"


class Store:
    """Exact-name lookup finds nothing; the by-id re-read answers from ``rows``."""

    def __init__(self, rows=None, name_hit=None):
        self.rows = rows or {}
        self.name_hit = name_hit
        self.log: list[tuple[str, tuple]] = []

    def fetch_one(self, sql, params=None):
        s = " ".join(sql.split())
        self.log.append((s, tuple(params or ())))
        if "allows_shared" in s:
            return {"allows_shared": True}
        if "entity_id::text = %s" in s:
            return self.rows.get(params[-1])
        if "lower(name) = lower(%s)" in s:
            return self.name_hit
        return None

    def fetch_all(self, sql, params=None):
        return []


@pytest.fixture
def env():
    vs = MagicMock()
    vs.search.return_value = [
        {"payload": {"entity_id": EID, "name": "Stale Name", "entity_type": "PERSON"}}
    ]
    emb = MagicMock()
    emb.embed.return_value = [0.0]
    _config._instances["vector_store"] = vs
    _config._instances["embedder"] = emb
    yield
    _config._instances.clear()


def _run(store):
    _config._instances["metadata_store"] = store
    return json.loads(tools._query_entity({"name": "stale"}, user_id="alice"))


def test_T1_a_qdrant_hit_with_no_postgres_row_is_not_found(env):
    out = _run(Store(rows={}))
    assert out == {"found": False, "name": "stale"}


def test_T2_the_answer_comes_from_the_row_not_the_payload(env):
    row = {
        "entity_id": EID,
        "name": "Real Name",
        "entity_type": "ORG",
        "aliases": ["R"],
        "context_tags": [],
        "mention_count": 5,
    }
    out = _run(Store(rows={EID: row}))
    assert out["found"] is True
    assert out["entity"]["name"] == "Real Name" and out["entity"]["type"] == "ORG"
    assert out["entity"]["mention_count"] == 5


def test_T3_the_reread_and_the_exact_lookup_both_apply_hide_and_visibility(env):
    store = Store(rows={})
    _run(store)
    for sql, params in [(s, p) for s, p in store.log if "FROM entities" in s]:
        assert "NOT EXISTS (SELECT 1 FROM entity_hidden h WHERE h.user_id = %s" in sql
        assert "scope = 'personal' AND user_id = %s" in sql
        assert params.count("alice") >= 2
