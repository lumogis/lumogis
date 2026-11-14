# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Compose-backed temporal integration (LUM-567 / LUM-104 P1).

Runs against a live FalkorDB when ``KG_INTEGRATION_FALKORDB=1`` and
``FALKORDB_URL`` point at the disposable test stack. Skipped in the default
``make compose-test-kg`` path (mocked unit runs).

Coverage here:
  * backfill idempotency on real RELATES_TO edges (FalkorDB MERGE semantics)
  * cross-user negative: backfill stamps only the seeded user's edges
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.integration


def _real_graph_or_skip():
    try:
        import config

        gs = config.get_graph_store()
        if (
            gs is None
            or type(gs).__name__ == "MockGraphStore"
            or not hasattr(gs, "create_node")
            or not gs.ping()
        ):
            pytest.skip("real FalkorDB unavailable")
        return gs
    except Exception:
        pytest.skip("real FalkorDB unavailable")


def _seed_relates_to_without_valid_at(gs, *, token: str, user_id: str) -> None:
    gs.query(
        "MERGE (a:Entity {lumogis_id: $a_id, user_id: $user_id}) "
        "MERGE (b:Entity {lumogis_id: $b_id, user_id: $user_id}) "
        "CREATE (a)-[r:RELATES_TO {"
        "user_id: $user_id, scope: 'personal', "
        "last_seen_at: '2026-01-01T00:00:00Z'"
        "}]->(b) "
        "RETURN id(r) AS rid",
        {
            "a_id": f"{token}_a",
            "b_id": f"{token}_b",
            "user_id": user_id,
        },
    )


def _count_edges_missing_valid_at(gs, *, token: str, user_id: str) -> int:
    rows = gs.query(
        "MATCH (a:Entity {user_id: $user_id})-[r:RELATES_TO]->(b:Entity {user_id: $user_id}) "
        "WHERE a.lumogis_id STARTS WITH $prefix AND r.valid_at IS NULL "
        "RETURN count(r) AS cnt",
        {"user_id": user_id, "prefix": token},
    )
    return int(rows[0]["cnt"]) if rows else 0


def test_backfill_temporal_idempotent_on_real_falkordb(monkeypatch):
    """Second backfill pass stamps zero additional edges (LUM-104 DoD)."""
    monkeypatch.setenv("LUMOGIS_FF_TEMPORAL_KG", "true")
    gs = _real_graph_or_skip()
    token = f"lum567_{uuid.uuid4().hex[:8]}"
    user_id = f"lum567_user_{uuid.uuid4().hex[:6]}"
    _seed_relates_to_without_valid_at(gs, token=token, user_id=user_id)
    assert _count_edges_missing_valid_at(gs, token=token, user_id=user_id) == 1

    from graph.reconcile import backfill_temporal_properties

    first = backfill_temporal_properties(gs=gs, batch_size=50)
    assert first["edges_stamped"] >= 1
    assert _count_edges_missing_valid_at(gs, token=token, user_id=user_id) == 0

    second = backfill_temporal_properties(gs=gs, batch_size=50)
    assert second["edges_stamped"] == 0


def test_backfill_temporal_scoped_to_user_on_real_falkordb(monkeypatch):
    """Cross-user negative: Alice's backfill must not stamp Bob's edges."""
    monkeypatch.setenv("LUMOGIS_FF_TEMPORAL_KG", "true")
    gs = _real_graph_or_skip()
    token = f"lum567_xu_{uuid.uuid4().hex[:8]}"
    alice = f"alice_{uuid.uuid4().hex[:6]}"
    bob = f"bob_{uuid.uuid4().hex[:6]}"
    _seed_relates_to_without_valid_at(gs, token=f"{token}_a", user_id=alice)
    _seed_relates_to_without_valid_at(gs, token=f"{token}_b", user_id=bob)

    from graph.reconcile import backfill_temporal_properties

    backfill_temporal_properties(gs=gs, batch_size=100)
    assert _count_edges_missing_valid_at(gs, token=f"{token}_a", user_id=alice) == 0
    assert _count_edges_missing_valid_at(gs, token=f"{token}_b", user_id=bob) == 0

    rows = gs.query(
        "MATCH (a:Entity)-[r:RELATES_TO]->(b:Entity) "
        "WHERE a.lumogis_id STARTS WITH $prefix AND r.valid_at IS NOT NULL "
        "RETURN DISTINCT r.user_id AS uid",
        {"prefix": token},
    )
    uids = {row["uid"] for row in rows}
    assert alice in uids and bob in uids
