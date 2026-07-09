# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Unit tests for services/entity_edges.py (LUM-291)."""

from unittest.mock import Mock

import pytest

import config
from services import entity_edges


def test_store_edge_rejects_off_allowlist():
    """The relation_type allowlist is the Cypher-injection guard — rejected
    before any DB or graph work."""
    ms = Mock()
    with pytest.raises(ValueError):
        entity_edges.store_edge(
            user_id="u",
            bank="b",
            src_entity_id="1",
            dst_entity_id="2",
            relation_type="DROP ALL",
            ms=ms,
        )
    ms.execute.assert_not_called()


def test_store_edge_inserts_uppercases_and_graph_off(monkeypatch):
    ms = Mock()
    ms.fetch_one.return_value = {"id": "edge1"}
    monkeypatch.setattr(config, "get_graph_store", lambda bank=None: None)
    eid = entity_edges.store_edge(
        user_id="u",
        bank="coding",
        src_entity_id="s",
        dst_entity_id="d",
        relation_type="depends_on",
        ms=ms,
    )
    assert eid == "edge1"
    sql, params = ms.execute.call_args.args
    assert "INSERT INTO entity_edges" in sql
    assert "DEPENDS_ON" in params  # normalised to uppercase


def test_store_edge_graph_enabled_projects_allowlisted_reltype(monkeypatch):
    ms = Mock()
    ms.fetch_one.return_value = {"id": "e"}
    gs = Mock()
    monkeypatch.setattr(config, "get_graph_store", lambda bank=None: gs)
    entity_edges.store_edge(
        user_id="u",
        bank="personal",
        src_entity_id="s",
        dst_entity_id="d",
        relation_type="RELATES_TO",
        ms=ms,
    )
    gs.query.assert_called_once()
    cypher = gs.query.call_args.args[0]
    assert "[r:RELATES_TO]" in cypher  # only allowlisted tokens reach Cypher
    # LUM-566: nodes keyed on lumogis_id (writer contract), scoped by user_id;
    # the old entity_id key must not reappear (it never matches a writer node).
    assert "lumogis_id" in cypher
    assert "user_id" in cypher
    assert "entity_id" not in cypher
    params = gs.query.call_args.args[1]
    assert set(params) == {"src", "dst", "user_id"}


def test_store_edge_graph_projection_failure_is_swallowed(monkeypatch):
    """A FalkorDB failure must not fail the Postgres write (Postgres is SoR)."""
    ms = Mock()
    ms.fetch_one.return_value = {"id": "e"}
    gs = Mock()
    gs.query.side_effect = RuntimeError("falkordb down")
    monkeypatch.setattr(config, "get_graph_store", lambda bank=None: gs)
    eid = entity_edges.store_edge(
        user_id="u",
        bank="personal",
        src_entity_id="s",
        dst_entity_id="d",
        relation_type="PART_OF",
        ms=ms,
    )
    assert eid == "e"


def test_store_edge_upsert_idempotent():
    """Same (user,bank,src,dst,rel) twice → one row, same durable id (ON CONFLICT)."""

    class _FakeStore:
        def __init__(self):
            self.rows = {}

        def execute(self, sql, params=None):
            if "INSERT INTO entity_edges" in sql:
                eid, uid, bank, src, dst, rel, _ev = params
                self.rows.setdefault((uid, bank, src, dst, rel), eid)  # DO NOTHING on conflict

        def fetch_one(self, sql, params=None):
            uid, bank, src, dst, rel = params
            key = (uid, bank, src, dst, rel)
            return {"id": self.rows[key]} if key in self.rows else None

    import config as _cfg

    ms = _FakeStore()
    # graph off
    import pytest as _pytest

    monkeypatch = _pytest.MonkeyPatch()
    monkeypatch.setattr(_cfg, "get_graph_store", lambda bank=None: None)
    try:
        a = entity_edges.store_edge(
            user_id="u",
            bank="coding",
            src_entity_id="s",
            dst_entity_id="d",
            relation_type="DEPENDS_ON",
            ms=ms,
        )
        b = entity_edges.store_edge(
            user_id="u",
            bank="coding",
            src_entity_id="s",
            dst_entity_id="d",
            relation_type="DEPENDS_ON",
            ms=ms,
        )
    finally:
        monkeypatch.undo()
    assert a == b
    assert len(ms.rows) == 1


def test_fetch_active_edges_for_memory_returns_rows():
    ms = Mock()
    ms.fetch_all.return_value = [
        {
            "bank": "coding",
            "src_entity_id": "s",
            "dst_entity_id": "d",
            "relation_type": "RELATES_TO",
        }
    ]
    rows = entity_edges.fetch_active_edges_for_memory("m1", user_id="u", ms=ms)
    assert rows == ms.fetch_all.return_value
    sql, params = ms.fetch_all.call_args.args
    assert "valid_until IS NULL" in sql
    assert params == ("m1", "u")


def test_purge_graph_projections_deletes_on_bank_graph(monkeypatch):
    coding_gs = Mock()
    personal_gs = Mock()
    stores = {"coding": coding_gs, "personal": personal_gs}

    def _gs(bank=None):
        if bank is None:
            return personal_gs
        return stores.get(bank)

    monkeypatch.setattr(config, "get_graph_store", _gs)
    entity_edges.purge_graph_projections_for_edges(
        [
            {
                "bank": "coding",
                "src_entity_id": "s1",
                "dst_entity_id": "d1",
                "relation_type": "RELATES_TO",
            },
            {
                "bank": "personal",
                "src_entity_id": "s2",
                "dst_entity_id": "d2",
                "relation_type": "PART_OF",
            },
        ],
        user_id="u",
    )
    coding_gs.query.assert_called_once()
    personal_gs.query.assert_called_once()
    coding_cypher = coding_gs.query.call_args.args[0]
    personal_cypher = personal_gs.query.call_args.args[0]
    assert "[r:RELATES_TO]" in coding_cypher
    assert "[r:PART_OF]" in personal_cypher
    assert "DELETE r" in coding_cypher
    # LUM-566: purge keys nodes on lumogis_id, scopes by user_id, and carries the
    # co-occurrence-preservation guard so it cannot delete the writer's aggregate
    # RELATES_TO edge. The old entity_id node key must be gone.
    for cypher in (coding_cypher, personal_cypher):
        assert "lumogis_id" in cypher
        assert "user_id" in cypher
        assert "r.co_occurrence_count IS NULL" in cypher
        assert "entity_id" not in cypher


def test_purge_graph_projections_skips_when_graph_disabled(monkeypatch):
    monkeypatch.setattr(config, "get_graph_store", lambda bank=None: None)
    entity_edges.purge_graph_projections_for_edges(
        [
            {
                "bank": "coding",
                "src_entity_id": "s",
                "dst_entity_id": "d",
                "relation_type": "RELATES_TO",
            }
        ],
        user_id="u",
    )


# --- LUM-566 node-aware behavioural fake ------------------------------------
#
# A minimal fake GraphStore that models node existence and edge state for the
# exact two statement shapes entity_edges emits (projection MERGE, purge DELETE).
# It reads the node-property KEY literally from the Cypher, so a MATCH keyed on a
# property the nodes were NOT seeded under binds nothing — i.e. the pre-fix
# `entity_id` Cypher no-ops against `lumogis_id`-seeded nodes, and this fake
# proves purge really deletes / really preserves co-occurrence, without FalkorDB.


class _NodeAwareGraph:
    def __init__(self):
        self.nodes: set[tuple[str, str]] = set()  # (lumogis_id, user_id)
        # (src, dst, rel) -> {"user_id": str, "co_occurrence_count": int | None}
        self.rels: dict[tuple[str, str, str], dict] = {}

    def add_node(self, lumogis_id, user_id):
        self.nodes.add((lumogis_id, user_id))

    def add_rel(self, src, dst, rel, *, user_id, co_occurrence_count=None):
        self.rels[(src, dst, rel)] = {
            "user_id": user_id,
            "co_occurrence_count": co_occurrence_count,
        }

    @staticmethod
    def _node_key(cypher):
        return "lumogis_id" if "lumogis_id:" in cypher else "entity_id"

    @staticmethod
    def _rel_type(cypher):
        marker = "[r:"
        if marker not in cypher:
            return ""
        i = cypher.index(marker) + len(marker)
        j = i
        while j < len(cypher) and (cypher[j].isalnum() or cypher[j] == "_"):
            j += 1
        return cypher[i:j]

    def _match_node(self, cypher, lumogis_id, user_id):
        # A node keyed on a property other than lumogis_id binds nothing.
        return self._node_key(cypher) == "lumogis_id" and (lumogis_id, user_id) in self.nodes

    def query(self, cypher, params=None):
        params = params or {}
        src, dst, uid = params.get("src"), params.get("dst"), params.get("user_id")
        rel = self._rel_type(cypher)
        both_nodes = self._match_node(cypher, src, uid) and self._match_node(cypher, dst, uid)
        if "DELETE r" in cypher:  # purge
            if not both_nodes:
                return []
            existing = self.rels.get((src, dst, rel))
            if existing is None or existing["user_id"] != uid:
                return []
            if (
                "co_occurrence_count IS NULL" in cypher
                and existing["co_occurrence_count"] is not None
            ):
                return []  # guard: never delete an aggregate co-occurrence edge
            del self.rels[(src, dst, rel)]
            return []
        if "MERGE (a)-[r:" in cypher:  # projection
            if not both_nodes:
                # MATCH-not-MERGE: no node -> no edge, no node created. The
                # RETURN count(r) aggregation still yields one row with 0.
                return [{"attached": 0}] if "RETURN count(r)" in cypher else []
            existing = self.rels.get((src, dst, rel))
            if existing is None:
                self.rels[(src, dst, rel)] = {
                    "user_id": uid,
                    "co_occurrence_count": None,
                }
            else:
                existing["user_id"] = uid  # MERGE onto existing (count preserved)
            return [{"attached": 1}] if "RETURN count(r)" in cypher else []
        raise AssertionError(f"_NodeAwareGraph: unrecognised statement: {cypher!r}")


def _fake_ms():
    ms = Mock()
    ms.fetch_one.return_value = {"id": "e"}
    return ms


def test_node_aware_fake_raises_on_unknown_statement():
    """The fake must fail loudly on an unrecognised shape, not pass silently."""
    fake = _NodeAwareGraph()
    with pytest.raises(AssertionError):
        fake.query("MATCH (n) RETURN n", {})


def test_project_edge_creates_edge_on_lumogis_id_nodes(monkeypatch):
    fake = _NodeAwareGraph()
    fake.add_node("s", "u")
    fake.add_node("d", "u")
    monkeypatch.setattr(config, "get_graph_store", lambda bank=None: fake)
    entity_edges.store_edge(
        user_id="u",
        bank="personal",
        src_entity_id="s",
        dst_entity_id="d",
        relation_type="RELATES_TO",
        ms=_fake_ms(),
    )
    assert ("s", "d", "RELATES_TO") in fake.rels
    assert fake.rels[("s", "d", "RELATES_TO")]["user_id"] == "u"


def test_project_edge_skips_when_endpoint_node_absent(monkeypatch):
    """MATCH-not-MERGE: a missing endpoint node yields no edge and no new node."""
    fake = _NodeAwareGraph()
    fake.add_node("s", "u")  # dst node deliberately absent
    monkeypatch.setattr(config, "get_graph_store", lambda bank=None: fake)
    entity_edges.store_edge(
        user_id="u",
        bank="personal",
        src_entity_id="s",
        dst_entity_id="d",
        relation_type="RELATES_TO",
        ms=_fake_ms(),
    )
    assert fake.rels == {}
    assert fake.nodes == {("s", "u")}  # no node created by projection


def _stamp_calls(ms):
    return [c for c in ms.execute.call_args_list if "graph_projected_at" in c.args[0]]


def test_store_edge_stamps_graph_projected_at_on_attach(monkeypatch):
    """LUM-580 — a live projection that attaches to both nodes stamps the row."""
    fake = _NodeAwareGraph()
    fake.add_node("s", "u")
    fake.add_node("d", "u")
    monkeypatch.setattr(config, "get_graph_store", lambda bank=None: fake)
    ms = _fake_ms()  # fetch_one -> {"id": "e"} (the durable edge id)
    entity_edges.store_edge(
        user_id="u",
        bank="personal",
        src_entity_id="s",
        dst_entity_id="d",
        relation_type="IMPLEMENTS",
        ms=ms,
    )
    stamps = _stamp_calls(ms)
    assert len(stamps) == 1
    assert stamps[0].args[1] == ("e",)  # stamps the durable id, not the throwaway


def test_store_edge_does_not_stamp_when_node_absent(monkeypatch):
    """LUM-580 — the first-mention race (dst node absent) must NOT stamp, so
    reconcile_entity_edges can replay the dropped edge on a later pass."""
    fake = _NodeAwareGraph()
    fake.add_node("s", "u")  # dst node deliberately absent
    monkeypatch.setattr(config, "get_graph_store", lambda bank=None: fake)
    ms = _fake_ms()
    entity_edges.store_edge(
        user_id="u",
        bank="personal",
        src_entity_id="s",
        dst_entity_id="d",
        relation_type="IMPLEMENTS",
        ms=ms,
    )
    assert _stamp_calls(ms) == []


def test_store_edge_does_not_stamp_when_graph_disabled(monkeypatch):
    """LUM-580 — with no graph backend the edge is never projected, so no stamp."""
    monkeypatch.setattr(config, "get_graph_store", lambda bank=None: None)
    ms = _fake_ms()
    entity_edges.store_edge(
        user_id="u",
        bank="personal",
        src_entity_id="s",
        dst_entity_id="d",
        relation_type="IMPLEMENTS",
        ms=ms,
    )
    assert _stamp_calls(ms) == []


def test_purge_deletes_pure_typed_projection(monkeypatch):
    fake = _NodeAwareGraph()
    fake.add_node("s", "u")
    fake.add_node("d", "u")
    fake.add_rel("s", "d", "RELATES_TO", user_id="u")  # no co_occurrence_count
    monkeypatch.setattr(config, "get_graph_store", lambda bank=None: fake)
    entity_edges.purge_graph_projections_for_edges(
        [
            {
                "bank": "personal",
                "src_entity_id": "s",
                "dst_entity_id": "d",
                "relation_type": "RELATES_TO",
            }
        ],
        user_id="u",
    )
    assert fake.rels == {}


def test_purge_is_user_scoped(monkeypatch):
    """Even with same-id nodes for both users, purge honours r.user_id."""
    fake = _NodeAwareGraph()
    for uid in ("owner", "attacker"):
        fake.add_node("s", uid)
        fake.add_node("d", uid)
    fake.add_rel("s", "d", "RELATES_TO", user_id="owner")
    monkeypatch.setattr(config, "get_graph_store", lambda bank=None: fake)
    entity_edges.purge_graph_projections_for_edges(
        [
            {
                "bank": "personal",
                "src_entity_id": "s",
                "dst_entity_id": "d",
                "relation_type": "RELATES_TO",
            }
        ],
        user_id="attacker",
    )
    assert fake.rels[("s", "d", "RELATES_TO")]["user_id"] == "owner"


def test_purge_preserves_cooccurrence_relates_to(monkeypatch):
    """The co_occurrence_count guard preserves the writer's aggregate edge, while
    a pure typed RELATES_TO (no count) on another pair is still deleted."""
    fake = _NodeAwareGraph()
    for lid in ("s", "d", "x", "y"):
        fake.add_node(lid, "u")
    fake.add_rel("s", "d", "RELATES_TO", user_id="u", co_occurrence_count=3)
    fake.add_rel("x", "y", "RELATES_TO", user_id="u")  # pure typed, no count
    monkeypatch.setattr(config, "get_graph_store", lambda bank=None: fake)
    entity_edges.purge_graph_projections_for_edges(
        [
            {
                "bank": "personal",
                "src_entity_id": "s",
                "dst_entity_id": "d",
                "relation_type": "RELATES_TO",
            },
            {
                "bank": "personal",
                "src_entity_id": "x",
                "dst_entity_id": "y",
                "relation_type": "RELATES_TO",
            },
        ],
        user_id="u",
    )
    assert ("s", "d", "RELATES_TO") in fake.rels  # aggregate preserved
    assert fake.rels[("s", "d", "RELATES_TO")]["co_occurrence_count"] == 3
    assert ("x", "y", "RELATES_TO") not in fake.rels  # pure typed deleted
