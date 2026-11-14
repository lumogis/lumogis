# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Live Postgres: ``merge_entities`` repoints typed ``entity_edges`` (LUM-598).

Covers the real SQL (unit tests in ``test_entity_merge.py`` only pin its shape):
repoint, duplicate drop, self-loop archive, ``graph_projected_at`` reset,
temporal columns preserved, archived-winner collision left alone, and (when
FalkorDB is up) a reconcile pass attaching the repointed edge to the winner.

Skips when Postgres is unreachable. Primary gate: ``make compose-test-integration``.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime
from datetime import timezone
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest
from tests.integration._pgguard import require_postgres

import config


@pytest.fixture
def ms():
    try:
        from adapters.postgres_store import PostgresStore

        store = PostgresStore(
            host=os.environ.get("POSTGRES_HOST", "postgres"),
            port=int(os.environ.get("POSTGRES_PORT", "5432")),
            user=os.environ.get("POSTGRES_USER", "lumogis"),
            password=os.environ.get("POSTGRES_PASSWORD", "lumogis-dev"),
            dbname=os.environ.get("POSTGRES_DB", "lumogis"),
        )
        if not store.ping():
            raise RuntimeError("postgres ping failed")
    except Exception as exc:  # noqa: BLE001
        require_postgres(exc)
    return store


def _entity(ms, uid, name):
    eid = str(uuid.uuid4())
    ms.execute(
        "INSERT INTO entities (entity_id, user_id, name, entity_type) "
        "VALUES (%s, %s, %s, 'PERSON')",
        (eid, uid, name),
    )
    return eid


def _edge(ms, uid, src, dst, rel="IMPLEMENTS", *, bank="coding", archived=False, stamped=True):
    eid = uuid.uuid4().hex
    ms.execute(
        "INSERT INTO entity_edges (id, user_id, bank, src_entity_id, dst_entity_id, "
        "relation_type, valid_from, valid_until, graph_projected_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (
            eid,
            uid,
            bank,
            src,
            dst,
            rel,
            datetime(2026, 1, 2, tzinfo=timezone.utc),
            datetime(2026, 3, 4, tzinfo=timezone.utc) if archived else None,
            datetime(2026, 2, 1, tzinfo=timezone.utc) if stamped else None,
        ),
    )
    return eid


def _row(ms, edge_id):
    return ms.fetch_one("SELECT * FROM entity_edges WHERE id = %s", (edge_id,))


def _merge(ms, winner, loser, uid):
    from services.entity_merge import merge_entities

    with (
        patch.object(config, "get_metadata_store", return_value=ms),
        patch.object(config, "get_vector_store", return_value=MagicMock()),
        patch.object(config, "get_embedder", return_value=MagicMock()),
        patch("services.entity_merge.hooks.fire_background"),
    ):
        return merge_entities(winner, loser, uid)


@pytest.fixture
def world(ms):
    uid = f"itest-{uuid.uuid4().hex[:10]}"
    w, lo, x = _entity(ms, uid, "W"), _entity(ms, uid, "L"), _entity(ms, uid, "X")
    yield ms, uid, w, lo, x
    ms.execute("DELETE FROM entity_edges WHERE user_id = %s", (uid,))
    ms.execute("DELETE FROM entities WHERE user_id = %s", (uid,))


def test_edges_repointed_with_stamp_reset_and_temporal_preserved(world):
    ms, uid, w, lo, x = world
    out_e = _edge(ms, uid, lo, x)
    in_e = _edge(ms, uid, x, lo, "DEPENDS_ON")
    archived = _edge(ms, uid, lo, x, "REPLACES", archived=True)
    before = _row(ms, out_e)

    _merge(ms, w, lo, uid)

    a, b = _row(ms, out_e), _row(ms, in_e)
    assert (a["src_entity_id"], a["dst_entity_id"]) == (w, x)
    assert (b["src_entity_id"], b["dst_entity_id"]) == (x, w)
    assert a["graph_projected_at"] is None and b["graph_projected_at"] is None
    for col in ("valid_from", "valid_until", "created_at", "evidence_id", "bank"):
        assert a[col] == before[col]
    # Archived history is not rewritten.
    assert _row(ms, archived)["src_entity_id"] == lo


def test_duplicate_of_active_winner_edge_is_dropped(world):
    ms, uid, w, lo, x = world
    keep = _edge(ms, uid, w, x)
    dup = _edge(ms, uid, lo, x)
    _merge(ms, w, lo, uid)
    assert _row(ms, dup) is None
    kept = _row(ms, keep)
    assert kept is not None and kept["graph_projected_at"] is not None  # untouched


def test_selfloops_archived_not_repointed(world):
    ms, uid, w, lo, x = world
    e1, e2, e3 = (
        _edge(ms, uid, lo, w),
        _edge(ms, uid, w, lo, "DEPENDS_ON"),
        _edge(ms, uid, lo, lo, "PART_OF"),
    )
    _merge(ms, w, lo, uid)
    for eid in (e1, e2, e3):
        row = _row(ms, eid)
        assert row["valid_until"] is not None
        assert not (row["src_entity_id"] == w and row["dst_entity_id"] == w)


def test_collision_with_archived_winner_edge_left_untouched(world):
    ms, uid, w, lo, x = world
    _edge(ms, uid, w, x, archived=True)
    live = _edge(ms, uid, lo, x)
    _merge(ms, w, lo, uid)  # must not violate the UNIQUE index
    row = _row(ms, live)
    assert row["src_entity_id"] == lo and row["valid_until"] is None


def test_other_users_edges_untouched(world):
    ms, uid, w, lo, x = world
    other = f"itest-{uuid.uuid4().hex[:10]}"
    foreign = _edge(ms, other, lo, x)
    try:
        _merge(ms, w, lo, uid)
        assert _row(ms, foreign)["src_entity_id"] == lo
    finally:
        ms.execute("DELETE FROM entity_edges WHERE user_id = %s", (other,))


def test_merge_then_reconcile_attaches_repointed_edge_to_winner(world, monkeypatch):
    """Merge, then ``reconcile_entity_edges`` attaches the edge to the WINNER node."""
    pytest.importorskip("plugins.graph")
    from plugins.graph.reconcile import reconcile_entity_edges
    from tests.integration.test_entity_edges_reconcile_falkordb_live import _live_stores_or_skip

    ms, uid, w, lo, x = world
    ms, gs = _live_stores_or_skip(monkeypatch)
    monkeypatch.setitem(config._instances, "metadata_store", ms)
    edge = _edge(ms, uid, lo, x, bank="personal", stamped=False)
    for n in (w, x):
        gs.query("MERGE (a {lumogis_id: $id, user_id: $uid})", {"id": n, "uid": uid})
    try:
        _merge(ms, w, lo, uid)
        assert _row(ms, edge)["graph_projected_at"] is None
        assert reconcile_entity_edges()["projected_ok"] >= 1
        got = gs.query(
            "MATCH (a {lumogis_id: $s, user_id: $uid})-[r:IMPLEMENTS]->"
            "(b {lumogis_id: $d, user_id: $uid}) RETURN count(r) AS c",
            {"s": w, "d": x, "uid": uid},
        )
        assert int(got[0]["c"]) == 1
        assert _row(ms, edge)["graph_projected_at"] is not None
    finally:
        for n in (w, x):
            gs.query(
                "MATCH (a {lumogis_id: $id, user_id: $uid}) DETACH DELETE a", {"id": n, "uid": uid}
            )
