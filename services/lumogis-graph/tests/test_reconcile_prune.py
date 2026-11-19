# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""The reconcile prune that follows an entity merge or undo (LUM-965).

Hermetic: a fake metadata store and a fake graph store that records Cypher.
``orchestrator/tests/integration/test_graph_merge_undo_falkordb_live.py`` runs
the same statements against a real FalkorDB.
"""

from __future__ import annotations

import pytest
from graph import reconcile
from graph import writer

import config

EID = "11111111-1111-4111-8111-111111111111"
UID = "alice"


class _MS:
    def __init__(
        self,
        *,
        involved=True,
        gate_fails=False,
        sessions=(),
        typed=(),
        active=(),
        relations=(),
        siblings=(),
        fail_on=(),
    ):
        self.involved = involved
        self.gate_fails = gate_fails
        self.sessions = list(sessions)
        self.typed = list(typed)
        self.active = list(active)
        self.relations = list(relations)
        self.siblings = list(siblings)
        self.fail_on = fail_on
        self.executed: list[tuple[str, tuple]] = []
        self.fetched: list[str] = []
        self.fetched_params: list[tuple[str, tuple]] = []

    def fetch_all(self, sql, params=None):
        self.fetched.append(sql)
        self.fetched_params.append((sql, tuple(params or ())))
        if "FROM entities WHERE" in sql and "graph_projected_at IS NULL" in sql:
            return [{"entity_id": EID, "name": "Meier", "entity_type": "PERSON", "user_id": UID}]
        if "SELECT evidence_id, evidence_type FROM entity_relations" in sql:
            return list(self.relations)
        if "FROM sessions" in sql:
            return list(self.sessions)
        if "SELECT DISTINCT relation_type FROM entity_edges" in sql:
            return [{"relation_type": t} for t in self.typed]
        if "FROM entity_edges" in sql:
            return list(self.active)
        if "FROM entity_relations a" in sql:
            return list(self.siblings)
        return []

    def fetch_one(self, sql, params=None):
        if "FROM entity_merges" in sql:
            if self.gate_fails:
                raise RuntimeError("no such table")
            return {"x": 1} if self.involved else None
        return None

    def execute(self, sql, params=None):
        for frag in self.fail_on:
            if frag in sql:
                raise RuntimeError("boom")
        self.executed.append((sql, params))


class _GS:
    def __init__(self, *, graph_sessions=(), fail_on=()):
        self.graph_sessions = list(graph_sessions)
        self.fail_on = fail_on
        self.queries: list[tuple[str, dict]] = []

    def query(self, cypher, params=None):
        self.queries.append((cypher, params or {}))
        for frag in self.fail_on:
            if frag in cypher:
                raise RuntimeError("graph down")
        if "RETURN s.lumogis_id" in cypher:
            return [{"sid": s} for s in self.graph_sessions]
        return []

    def deletes(self):
        return [(c, p) for c, p in self.queries if "DELETE r" in c]


@pytest.fixture
def run(monkeypatch):
    def _run(ms, gs):
        calls = []
        monkeypatch.setattr(config, "get_metadata_store", lambda: ms)
        monkeypatch.setattr(config, "get_graph_store", lambda bank=None: gs)
        monkeypatch.setattr(config, "_resolve_graph_bank", lambda bank: "personal", raising=False)
        monkeypatch.setattr(config, "get_graph_max_cooccurrence_pairs", lambda: 100)
        monkeypatch.setattr(
            writer, "project_entity", lambda *a, **k: calls.append(("project", k["evidence_id"]))
        )
        monkeypatch.setattr(writer, "_update_cooccurrence_edges", lambda *a, **k: None)
        counter = reconcile.reconcile_entities()
        return counter, calls

    return _run


def test_G7_gate_entity_without_a_merge_row_gets_no_prune(run):
    ms, gs = _MS(involved=False), _GS()
    run(ms, gs)
    assert gs.queries == []


def test_G7_gate_read_failure_skips_the_prune_without_failing_the_entity(run):
    ms, gs = _MS(gate_fails=True), _GS()
    counter, _ = run(ms, gs)
    assert gs.queries == [] and counter["projected_ok"] == 1
    assert counter.get("prune_failed", 0) == 0


def test_G1_prune_runs_after_replay_with_the_replay_list_as_keep_set(run):
    ms = _MS(
        relations=[
            {"evidence_id": "/d/a.md", "evidence_type": "DOCUMENT"},
            {"evidence_id": "sess-1", "evidence_type": "SESSION"},
        ]
    )
    gs = _GS()
    _, calls = run(ms, gs)
    assert [c[1] for c in calls] == ["/d/a.md", "sess-1"]
    mentions = [(c, p) for c, p in gs.queries if "MENTIONS" in c]
    assert len(mentions) == 1 and mentions[0][1]["keep"] == ["/d/a.md", "sess-1"]
    for cypher, _p in gs.queries:
        assert "(e:Person " in cypher  # every statement names the label ...
        assert "MATCH (e " not in cypher and "MATCH (e)" not in cypher  # ... never unlabelled


def test_G1_entity_without_relations_keeps_the_stub(run):
    ms, gs = _MS(relations=[]), _GS()
    run(ms, gs)
    mentions = [p for c, p in gs.queries if "MENTIONS" in c]
    assert mentions[0]["keep"] == [EID]


def test_G2_discussed_in_prunes_only_sessions_postgres_says_lost_the_entity(run):
    ms = _MS(sessions=[{"sid": "s-gone"}])
    gs = _GS(graph_sessions=["s-gone", "s-legacy", "s-keep"])
    run(ms, gs)
    deletes = [p for c, p in gs.deletes() if "DISCUSSED_IN" in c]
    assert deletes == [{"eid": EID, "uid": UID, "prune": ["s-gone"]}]
    sql = next(s for s in ms.fetched if "FROM sessions" in s)
    assert "cardinality(entity_ids), 0) > 0" in sql  # empty entity_ids are never pruned


def test_G2_no_delete_when_nothing_to_prune(run):
    ms, gs = _MS(sessions=[]), _GS(graph_sessions=["s-legacy"])
    run(ms, gs)
    assert not [1 for c, _ in gs.queries if "DISCUSSED_IN" in c and "DELETE" in c]


def test_G3_typed_list_excludes_protected_types_and_active_are_strings(run):
    ms = _MS(
        typed=["WORKS_WITH", "MENTIONS", "DEPENDS_ON", "KNOWS", "SUPERSEDES"],
        active=[{"src_entity_id": EID, "dst_entity_id": "x", "relation_type": "KNOWS"}],
    )
    gs = _GS()
    run(ms, gs)
    (params,) = [p for c, p in gs.queries if "type(r) IN $typed" in c]
    assert params["typed"] == ["KNOWS", "WORKS_WITH"]
    assert params["active"] == [f"{EID}|x|KNOWS"]


def test_G6_typed_edges_are_bound_to_the_default_stores_bank(run):
    ms, gs = _MS(typed=["KNOWS"]), _GS()
    run(ms, gs)
    edge_reads = [(s, p) for s, p in ms.fetched_params if "FROM entity_edges" in s]
    assert edge_reads and all("bank = %s" in s for s, _ in edge_reads)
    # The bound value is the default store's resolved bank ("personal" in ``run``),
    # never another bank such as "coding".
    assert all("personal" in p and "coding" not in p for _, p in edge_reads)


def test_G5_cooccurrence_prune_uses_sibling_ids_and_the_count_guard(run):
    ms = _MS(siblings=[{"sibling_id": "sib-1", "shared": 2}])
    gs = _GS()
    run(ms, gs)
    (cypher, params) = next((c, p) for c, p in gs.queries if "RELATES_TO" in c)
    assert "r.co_occurrence_count IS NOT NULL" in cypher
    assert params["siblings"] == ["sib-1"]


def test_G5_cooccurrence_prune_skipped_at_the_pair_cap(run, monkeypatch):
    ms = _MS(siblings=[{"sibling_id": f"s{i}", "shared": 1} for i in range(3)])
    gs = _GS()
    monkeypatch.setattr(config, "get_graph_max_cooccurrence_pairs", lambda: 3, raising=False)
    # ``run`` sets the cap to 100; set it again after wiring.
    orig = reconcile.reconcile_entities

    def go():
        monkeypatch.setattr(config, "get_metadata_store", lambda: ms)
        monkeypatch.setattr(config, "get_graph_store", lambda bank=None: gs)
        monkeypatch.setattr(config, "_resolve_graph_bank", lambda bank: "personal", raising=False)
        monkeypatch.setattr(writer, "project_entity", lambda *a, **k: None)
        monkeypatch.setattr(writer, "_update_cooccurrence_edges", lambda *a, **k: None)
        return orig()

    go()
    assert not [1 for c, _ in gs.queries if "RELATES_TO" in c]


def test_G4_prune_failure_is_counted_unstamped_and_projection_still_counts(run, caplog):
    ms, gs = _MS(), _GS(fail_on=("MENTIONS",))
    with caplog.at_level("WARNING"):
        counter, _ = run(ms, gs)
    assert counter["prune_failed"] == 1 and counter["projected_ok"] == 1
    assert counter["stamped"] == 0  # the row is stale again, so it is not reported stamped
    unstamp = [p for s, p in ms.executed if "SET graph_projected_at = NULL" in s]
    assert unstamp == [(EID, UID)]
    assert any("prune failed" in r.message for r in caplog.records)
