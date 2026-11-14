# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Reconcile and backfill paths that had no tests (LUM-54).

Hermetic: fakes for the metadata and graph stores. Each section names the
``graph/reconcile.py`` pass it covers. The happy paths of the per-table passes
and the typed ``entity_edges`` replay are covered in Core's
``orchestrator/tests/premium/test_graph_reconcile.py`` and the live
``orchestrator/tests/integration/test_entity_edges_reconcile_*`` modules; this
file covers error, retry and resume paths, per-user isolation and input sizes.
"""

from __future__ import annotations

import re

import pytest
from graph import reconcile
from graph import writer

import config

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _MS:
    """Metadata store: canned ``fetch_all`` rows by SQL substring, records writes."""

    def __init__(self, rows: dict[str, list[dict]] | None = None, *, fail_on: tuple = ()):
        self.rows = rows or {}
        self.fail_on = fail_on  # substrings whose execute()/fetch_all() raise
        self.executed: list[tuple[str, tuple | None]] = []
        self.fetched: list[tuple[str, tuple | None]] = []

    def _maybe_fail(self, sql: str) -> None:
        for frag in self.fail_on:
            if frag in sql:
                raise RuntimeError(f"simulated failure on {frag!r}")

    def fetch_all(self, sql, params=None):
        self.fetched.append((sql, params))
        self._maybe_fail(sql)
        for frag, rows in self.rows.items():
            if frag in sql:
                return list(rows)
        return []

    def fetch_one(self, sql, params=None):
        return None

    def execute(self, sql, params=None):
        self._maybe_fail(sql)
        self.executed.append((sql, params))

    def updates(self, frag: str) -> list[tuple | None]:
        return [p for s, p in self.executed if frag in s]


class _GS:
    """Graph store: canned ``query`` rows by Cypher substring, records queries."""

    def __init__(self, rows: dict[str, list[dict]] | None = None, *, fail_on: tuple = ()):
        self.rows = rows or {}
        self.fail_on = fail_on
        self.queries: list[tuple[str, dict]] = []

    def query(self, cypher, params=None):
        self.queries.append((cypher, params or {}))
        for frag in self.fail_on:
            if frag in cypher:
                raise RuntimeError(f"simulated graph failure on {frag!r}")
        for frag, rows in self.rows.items():
            if frag in cypher:
                return list(rows)
        return []

    def ran(self, frag: str) -> list[dict]:
        return [p for c, p in self.queries if frag in c]


def _wire(monkeypatch, ms, gs) -> None:
    monkeypatch.setattr(config, "get_metadata_store", lambda: ms)
    monkeypatch.setattr(config, "get_graph_store", lambda bank=None: gs)


# ---------------------------------------------------------------------------
# reconcile_entities — stub relation, partial failure, resume
# ---------------------------------------------------------------------------


class _EntityWorld:
    """Stateful store + graph for driving the REAL ``writer.project_entity``.

    ``entities`` rows are stale while ``graph_projected_at`` is None; the
    writer's stamp and the reconcile unstamp flip that. ``create_edge`` raises
    for evidence ids in ``fail_evidence``, which is how a mid-entity failure is
    injected.
    """

    def __init__(self):
        self.entities: dict[tuple[str, str], dict] = {}
        self.relations: dict[tuple[str, str], list[dict]] = {}
        self.nodes: dict[tuple[str, str], str] = {}
        self.mentions: set[tuple[str, str, str]] = set()  # (user, entity, evidence)
        self.fail_evidence: set[str] = set()
        self._ids: dict[str, tuple[str, str]] = {}

    # -- metadata store ----------------------------------------------------
    def fetch_all(self, sql, params=None):
        if "FROM entities WHERE" in sql:
            return [
                {"entity_id": eid, "name": e["name"], "entity_type": "PERSON", "user_id": uid}
                for (uid, eid), e in self.entities.items()
                if e["graph_projected_at"] is None
            ]
        if "FROM entity_relations" in sql and "WHERE source_id" in sql:
            eid, uid = params
            return list(self.relations.get((uid, eid), []))
        return []  # co-occurrence siblings: none

    def fetch_one(self, sql, params=None):
        return None

    def execute(self, sql, params=None):
        if sql.startswith("UPDATE entities SET graph_projected_at"):
            eid, uid = params
            stamp = None if "= NULL" in sql else "now"
            self.entities[(uid, eid)]["graph_projected_at"] = stamp

    # -- graph store -------------------------------------------------------
    def create_node(self, labels, properties):
        key = (properties["user_id"], properties["lumogis_id"])
        nid = self.nodes.setdefault(key, f"n{len(self.nodes)}")
        self._ids[nid] = key
        return nid

    def create_edge(self, from_id, to_id, rel_type, properties):
        evidence = properties.get("evidence_id")
        if evidence in self.fail_evidence:
            raise RuntimeError("simulated MENTIONS write failure")
        uid, eid = self._ids[to_id]
        self.mentions.add((uid, eid, evidence))

    def query(self, cypher, params=None):
        return []

    # -- seeding -----------------------------------------------------------
    def add_entity(self, uid, eid, evidence_ids):
        self.entities[(uid, eid)] = {"name": eid, "graph_projected_at": None}
        self.relations[(uid, eid)] = [
            {"evidence_id": ev, "evidence_type": "DOCUMENT"} for ev in evidence_ids
        ]


@pytest.fixture
def world(monkeypatch):
    w = _EntityWorld()
    _wire(monkeypatch, w, w)
    return w


def test_entity_without_relations_projects_a_stub_node(monkeypatch):
    calls: list[dict] = []
    monkeypatch.setattr(writer, "project_entity", lambda gs, **kw: calls.append(kw))
    ms = _MS(
        {
            "FROM entities WHERE": [
                {"entity_id": "e1", "name": "Ada", "entity_type": "PERSON", "user_id": "alice"}
            ]
        }
    )
    _wire(monkeypatch, ms, _GS())

    result = reconcile.reconcile_entities()

    assert result["projected_ok"] == 1
    assert [(c["evidence_id"], c["evidence_type"]) for c in calls] == [("e1", "ENTITY")]
    # The relations lookup is scoped to the entity's own user.
    rel_params = [p for s, p in ms.fetched if "FROM entity_relations" in s]
    assert rel_params == [("e1", "alice")]


def test_reconcile_runs_one_aggregate_per_entity(monkeypatch):
    """LUM-952: co-occurrence once per stale entity, not once per evidence row."""
    projected: list[bool] = []
    aggregated: list[tuple] = []
    monkeypatch.setattr(
        writer, "project_entity", lambda gs, **kw: projected.append(kw["update_cooccurrence"])
    )
    monkeypatch.setattr(
        writer,
        "_update_cooccurrence_edges",
        lambda gs, entity_id, user_id, evidence_id: aggregated.append(
            (entity_id, user_id, evidence_id)
        ),
    )
    ms = _MS(
        {
            "FROM entities WHERE": [
                {"entity_id": "e1", "name": "Ada", "entity_type": "PERSON", "user_id": "alice"}
            ],
            "FROM entity_relations": [
                {"evidence_id": f"ev{i}", "evidence_type": "DOCUMENT"} for i in range(5)
            ],
        }
    )
    _wire(monkeypatch, ms, _GS())

    reconcile.reconcile_entities()

    assert projected == [False] * 5
    assert aggregated == [("e1", "alice", None)]


def test_stub_only_entity_runs_no_cooccurrence(monkeypatch):
    aggregated: list = []
    monkeypatch.setattr(writer, "project_entity", lambda gs, **kw: None)
    monkeypatch.setattr(writer, "_update_cooccurrence_edges", lambda *a: aggregated.append(a))
    ms = _MS(
        {
            "FROM entities WHERE": [
                {"entity_id": "e1", "name": "Ada", "entity_type": "PERSON", "user_id": "alice"}
            ]
        }
    )
    _wire(monkeypatch, ms, _GS())

    reconcile.reconcile_entities()

    assert aggregated == []


def test_entity_partial_failure_leaves_row_stale_and_next_pass_resumes(world):
    """A failure on the 2nd of 3 relations must leave the entity stale.

    The writer stamps graph_projected_at after each relation, so before the
    LUM-54 fix the 1st relation's stamp survived the failure: the row stopped
    being stale and the 2nd and 3rd MENTIONS edges were never retried.
    """
    world.add_entity("alice", "e1", ["ev1", "ev2", "ev3"])
    world.fail_evidence = {"ev2"}

    first = reconcile.reconcile_entities()

    assert first["projected_failed"] == 1 and first["stamped"] == 0
    assert world.mentions == {("alice", "e1", "ev1")}  # ev3 not attempted after the break
    assert world.entities[("alice", "e1")]["graph_projected_at"] is None  # still stale

    world.fail_evidence = set()  # backend recovered
    second = reconcile.reconcile_entities()

    assert second["scanned"] == 1 and second["projected_ok"] == 1
    assert world.mentions == {("alice", "e1", ev) for ev in ("ev1", "ev2", "ev3")}
    assert world.entities[("alice", "e1")]["graph_projected_at"] == "now"

    third = reconcile.reconcile_entities()
    assert third["scanned"] == 0  # re-run after success finds nothing


def test_entity_failure_does_not_stop_other_entities_in_the_pass(world):
    world.add_entity("alice", "bad", ["ev-bad"])
    world.add_entity("bob", "good", ["ev-good"])
    world.fail_evidence = {"ev-bad"}

    result = reconcile.reconcile_entities()

    assert result["scanned"] == 2
    assert result["projected_ok"] == 1 and result["projected_failed"] == 1
    assert ("bob", "good", "ev-good") in world.mentions
    assert world.entities[("bob", "good")]["graph_projected_at"] == "now"
    assert world.entities[("alice", "bad")]["graph_projected_at"] is None


def test_entity_failure_on_first_relation_issues_no_unstamp(monkeypatch):
    """Nothing was stamped by this pass, so there is nothing to clear."""

    def boom(gs, **kw):
        raise RuntimeError("down")

    monkeypatch.setattr(writer, "project_entity", boom)
    ms = _MS(
        {
            "FROM entities WHERE": [
                {"entity_id": "e1", "name": "n", "entity_type": "PERSON", "user_id": "u"}
            ],
            "FROM entity_relations": [{"evidence_id": "a", "evidence_type": "DOCUMENT"}],
        }
    )
    _wire(monkeypatch, ms, _GS())

    result = reconcile.reconcile_entities()

    assert result["projected_failed"] == 1
    assert ms.updates("graph_projected_at = NULL") == []


def test_entity_unstamp_failure_is_swallowed(monkeypatch):
    seen: list[str] = []

    def second_fails(gs, **kw):
        seen.append(kw["evidence_id"])
        if len(seen) == 2:
            raise RuntimeError("down")

    monkeypatch.setattr(writer, "project_entity", second_fails)
    ms = _MS(
        {
            "FROM entities WHERE": [
                {"entity_id": "e1", "name": "n", "entity_type": "PERSON", "user_id": "u"}
            ],
            "FROM entity_relations": [
                {"evidence_id": "a", "evidence_type": "DOCUMENT"},
                {"evidence_id": "b", "evidence_type": "DOCUMENT"},
            ],
        },
        fail_on=("graph_projected_at = NULL",),
    )
    _wire(monkeypatch, ms, _GS())

    result = reconcile.reconcile_entities()  # must not raise

    assert result["projected_failed"] == 1


# ---------------------------------------------------------------------------
# reconcile_shared_entities — stamp only on success, per-row isolation
# ---------------------------------------------------------------------------


def _shared_row(proj, src, uid):
    return {
        "entity_id": proj,
        "published_from": src,
        "name": "N",
        "entity_type": "PERSON",
        "user_id": uid,
    }


@pytest.fixture
def shared(monkeypatch):
    import projection

    calls: list[dict] = []
    outcome: dict[str, object] = {}  # proj_entity_id -> True | False | Exception

    def fake_project(**kw):
        calls.append(kw)
        res = outcome.get(kw["proj_entity_id"], True)
        if isinstance(res, Exception):
            raise res
        return res

    monkeypatch.setattr(projection, "project_entity_into_graph", fake_project)
    return calls, outcome


def test_shared_success_stamps_each_row_under_its_own_user(monkeypatch, shared):
    calls, _ = shared
    ms = _MS(
        {"scope = 'shared'": [_shared_row("p1", "s1", "alice"), _shared_row("p2", "s2", "bob")]}
    )
    _wire(monkeypatch, ms, _GS())

    result = reconcile.reconcile_shared_entities()

    assert result["projected_ok"] == 2 and result["stamped"] == 2
    assert [(c["src_entity_id"], c["proj_entity_id"], c["actor_user_id"]) for c in calls] == [
        ("s1", "p1", "alice"),
        ("s2", "p2", "bob"),
    ]
    assert all(c["target_scope"] == "shared" for c in calls)
    assert ms.updates("SET graph_projected_at = NOW()") == [("p1", "alice"), ("p2", "bob")]


def test_shared_false_or_error_is_failed_unstamped_and_pass_continues(monkeypatch, shared):
    _, outcome = shared
    outcome["p1"] = False
    outcome["p2"] = RuntimeError("falkordb down")
    ms = _MS(
        {
            "scope = 'shared'": [
                _shared_row("p1", "s1", "u"),
                _shared_row("p2", "s2", "u"),
                _shared_row("p3", "s3", "u"),
            ]
        }
    )
    _wire(monkeypatch, ms, _GS())

    result = reconcile.reconcile_shared_entities()

    assert result["projected_failed"] == 2
    assert result["projected_ok"] == 1 and result["stamped"] == 1
    assert ms.updates("SET graph_projected_at = NOW()") == [("p3", "u")]


def test_shared_stamp_failure_is_swallowed(monkeypatch, shared):
    ms = _MS({"scope = 'shared'": [_shared_row("p1", "s1", "u")]}, fail_on=("UPDATE entities",))
    _wire(monkeypatch, ms, _GS())

    result = reconcile.reconcile_shared_entities()

    assert result["projected_ok"] == 1 and result["stamped"] == 0


def test_shared_row_without_published_from_is_skipped(monkeypatch, shared):
    calls, _ = shared
    ms = _MS({"scope = 'shared'": [_shared_row("p1", "", "u")]})
    _wire(monkeypatch, ms, _GS())

    result = reconcile.reconcile_shared_entities()

    assert calls == [] and result["projected_ok"] == 0 and ms.executed == []


def test_shared_pass_is_a_noop_without_a_graph(monkeypatch, shared):
    calls, _ = shared
    ms = _MS({"scope = 'shared'": [_shared_row("p1", "s1", "u")]})
    _wire(monkeypatch, ms, None)

    result = reconcile.reconcile_shared_entities()

    assert result["scanned"] == 0 and calls == [] and ms.fetched == []


# ---------------------------------------------------------------------------
# reconcile_sessions — a failed row is counted, not stamped, and not fatal
# ---------------------------------------------------------------------------


def test_session_failure_is_counted_and_pass_continues(monkeypatch):
    projected: list[str] = []

    def fake_project_session(gs, **kw):
        if kw["session_id"] == "bad":
            raise RuntimeError("down")
        projected.append(kw["session_id"])

    monkeypatch.setattr(writer, "project_session", fake_project_session)
    ms = _MS(
        {
            "FROM sessions": [
                {"session_id": "bad", "user_id": "u"},
                {"session_id": "ok", "user_id": "u"},
            ]
        }
    )
    _wire(monkeypatch, ms, _GS())

    result = reconcile.reconcile_sessions()

    assert result == {
        **result,
        "scanned": 2,
        "projected_ok": 1,
        "projected_failed": 1,
        "stamped": 1,
    }
    assert projected == ["ok"]


# ---------------------------------------------------------------------------
# garbage_collect_orphan_nodes — deletes only orphans, per user (LUM-54, LUM-952)
# ---------------------------------------------------------------------------

_GC_SCAN = "RETURN id(e) AS nid, e.user_id AS uid"
_GC_DELETE = "DETACH DELETE n"


def _scan_row(uid, lid, label="Person", nid=None):
    return {
        "nid": nid if nid is not None else hash((uid, lid)) & 0xFFFF,
        "uid": uid,
        "lid": lid,
        "labels": [label],
    }


def _gc(
    monkeypatch,
    live: list[tuple[str, str]],
    graph: list,
    *,
    ms_fail=(),
    gs_fail=(),
    limit=None,
    dry_run=False,
    force=False,
):
    ms = _MS({"FROM entities": [{"user_id": u, "entity_id": e} for u, e in live]}, fail_on=ms_fail)
    rows = [g if isinstance(g, dict) else _scan_row(*g) for g in graph]
    gs = _DeletingGS({_GC_SCAN: rows}, fail_on=gs_fail)
    _wire(monkeypatch, ms, gs)
    result = reconcile.garbage_collect_orphan_nodes(limit=limit, dry_run=dry_run, force=force)
    return result, gs, ms


class _DeletingGS(_GS):
    """Like FalkorDB, the batched delete reports how many rows it deleted."""

    def __init__(self, *a, matched=None, **kw):
        super().__init__(*a, **kw)
        self.matched = matched  # optional cap: rows that still match at delete time

    def query(self, cypher, params=None):
        rows = super().query(cypher, params)
        if _GC_DELETE in cypher:
            n = len((params or {}).get("rows", []))
            return [{"deleted": n if self.matched is None else min(n, self.matched)}]
        return rows


# A live entity no scanned node matches: keeps the live set non-empty, so the
# "empty live set" refusal does not apply where a test means "all orphaned".
_KEEP = [("keeper", "k0")]


def _deleted(gs) -> list[tuple]:
    return sorted((r["uid"], r["lid"]) for p in gs.ran(_GC_DELETE) for r in p["rows"])


def test_gc_scans_entity_and_type_labels_only(monkeypatch):
    _, gs, _ = _gc(monkeypatch, live=_KEEP, graph=[])
    scan = next(c for c, _ in gs.queries if _GC_SCAN in c)
    for label in ("Entity", "Person", "Organisation", "Project", "Concept"):
        assert f"e:{label}" in scan
    # Never label-agnostic: evidence, code and Memory nodes share lumogis_id/user_id.
    for label in ("Document", "Session", "Note", "AudioMemo", "Component", "Library"):
        assert label not in scan
    assert scan.startswith("MATCH (e) WHERE (")


def test_gc_reads_graph_before_postgres(monkeypatch):
    order: list[str] = []

    class _OrderedMS(_MS):
        def fetch_all(self, sql, params=None):
            order.append("pg")
            return super().fetch_all(sql, params)

    class _OrderedGS(_GS):
        def query(self, cypher, params=None):
            order.append("graph" if _GC_SCAN in cypher else "other")
            return super().query(cypher, params)

    _wire(monkeypatch, _OrderedMS({"FROM entities": []}), _OrderedGS({_GC_SCAN: []}))
    reconcile.garbage_collect_orphan_nodes()
    assert order[:2] == ["graph", "pg"]


def test_gc_deletes_only_orphans_and_matches_user_and_id(monkeypatch):
    result, gs, _ = _gc(
        monkeypatch,
        live=[("alice", "e1"), ("bob", "e2")],
        # bob's "e1" shares an id with alice's live entity but has no row of its own.
        graph=[("alice", "e1"), ("bob", "e2"), ("bob", "e1"), ("alice", "gone", "Concept")],
    )

    assert result["scanned"] == 4
    assert result["orphans_found"] == 2 and result["deleted_ok"] == 2
    assert result["orphans_by_label"] == {"Person": 1, "Concept": 1}
    assert _deleted(gs) == [("alice", "gone"), ("bob", "e1")]


def test_gc_delete_cypher_carries_label_predicate_and_id(monkeypatch):
    _, gs, _ = _gc(monkeypatch, live=_KEEP, graph=[("u", "a")])
    delete = next(c for c, _ in gs.queries if _GC_DELETE in c)
    assert "id(n) = r.nid" in delete
    assert "n.user_id = r.uid AND n.lumogis_id = r.lid" in delete
    assert "n:Person" in delete and "n:Entity" in delete


def test_gc_ignores_nodes_missing_user_or_id(monkeypatch):
    result, gs, _ = _gc(monkeypatch, live=_KEEP, graph=[(None, "x"), ("u", None)])

    assert result["scanned"] == 2 and result["orphans_found"] == 0
    assert gs.ran(_GC_DELETE) == []


def test_gc_limit_caps_deletes_but_counts_every_orphan(monkeypatch):
    result, gs, _ = _gc(monkeypatch, live=_KEEP, graph=[("u", f"e{i}") for i in range(5)], limit=2)

    assert result["orphans_found"] == 5 and result["deleted_ok"] == 2
    assert len(_deleted(gs)) == 2


def test_gc_delete_failure_is_counted_and_pass_continues(monkeypatch):
    result, _, _ = _gc(
        monkeypatch, live=_KEEP, graph=[("u", "a"), ("u", "b")], gs_fail=(_GC_DELETE,)
    )

    assert result["orphans_found"] == 2
    assert result["deleted_ok"] == 0 and result["deleted_failed"] == 2


def test_gc_aborts_without_deleting_when_postgres_read_fails(monkeypatch):
    """An unreadable live set must never be treated as 'nothing is live'."""
    result, gs, _ = _gc(monkeypatch, live=_KEEP, graph=[("u", "a")], ms_fail=("FROM entities",))

    assert result["deleted_ok"] == 0
    assert gs.ran(_GC_DELETE) == []


def test_gc_aborts_when_graph_scan_fails(monkeypatch):
    result, gs, ms = _gc(monkeypatch, live=_KEEP, graph=[], gs_fail=(_GC_SCAN,))

    assert result["scanned"] == 0 and gs.ran(_GC_DELETE) == []
    assert ms.fetched == []  # the scan comes first, so Postgres is never read


def test_gc_is_a_noop_without_a_graph(monkeypatch):
    ms = _MS()
    _wire(monkeypatch, ms, None)

    result = reconcile.garbage_collect_orphan_nodes()

    assert result["scanned"] == 0 and ms.fetched == []


def test_gc_empty_and_large_inputs(monkeypatch):
    empty, gs, _ = _gc(monkeypatch, live=_KEEP, graph=[])
    assert empty["scanned"] == 0 and gs.ran(_GC_DELETE) == []

    n = 20_000
    live = [("u", f"e{i}") for i in range(0, n, 2)]
    graph = [_scan_row("u", f"e{i}", nid=i) for i in range(n)]
    # 10k personal orphans trip the guard; force bypasses it for this bookkeeping check.
    big, gs, _ = _gc(monkeypatch, live=live, graph=graph, limit=500, force=True)
    assert big["scanned"] == n
    assert big["orphans_found"] == n // 2 and big["deleted_ok"] == 500
    deleted = _deleted(gs)
    assert len(deleted) == 500 and all(int(lid[1:]) % 2 == 1 for _, lid in deleted)
    assert max(len(p["rows"]) for p in gs.ran(_GC_DELETE)) <= 200  # batched


# --- the mass-delete guard (personal labels only) ---------------------------


def test_gc_cap_alone_refuses(monkeypatch):
    # 501 orphans of 100 000 scanned: ratio well under 20%, cap still refuses.
    live = [("u", f"live{i}") for i in range(99_499)]
    graph = [_scan_row("u", f"live{i}", nid=i) for i in range(99_499)]
    graph += [_scan_row("u", f"gone{i}", nid=200_000 + i) for i in range(501)]
    result, gs, _ = _gc(monkeypatch, live=live, graph=graph)
    assert result["refused"] is True and result["deleted_ok"] == 0
    assert gs.ran(_GC_DELETE) == []


def test_gc_ratio_alone_refuses(monkeypatch):
    # 30 of 100 scanned: under the cap, over 20% with the 25-orphan floor reached.
    live = [("u", f"live{i}") for i in range(70)]
    graph = [("u", f"live{i}") for i in range(70)] + [("u", f"gone{i}") for i in range(30)]
    result, gs, _ = _gc(monkeypatch, live=live, graph=graph)
    assert result["refused"] is True and gs.ran(_GC_DELETE) == []


def test_gc_ratio_rule_ignores_small_counts(monkeypatch):
    # 1 orphan of 3: 33%, but under the 25-orphan floor, so it is deleted.
    result, gs, _ = _gc(
        monkeypatch, live=[("u", "a"), ("u", "b")], graph=[("u", "a"), ("u", "b"), ("u", "c")]
    )
    assert result["refused"] is False and _deleted(gs) == [("u", "c")]


def test_gc_force_overrides_guard(monkeypatch):
    graph = [("u", f"gone{i}") for i in range(30)]
    result, gs, _ = _gc(monkeypatch, live=_KEEP, graph=graph, force=True)
    assert result["refused"] is False and result["deleted_ok"] == 30


def test_gc_entity_orphans_are_not_guarded(monkeypatch):
    """Shared-projection teardown after unshare or purge must never be held back."""
    shared = [_scan_row("u", f"sh{i}", label="Entity") for i in range(40)]
    personal = [("u", f"p{i}") for i in range(30)]
    result, gs, _ = _gc(monkeypatch, live=_KEEP, graph=shared + personal)
    assert result["refused"] is True
    assert sorted(lid for _, lid in _deleted(gs)) == sorted(f"sh{i}" for i in range(40))
    assert result["deleted_ok"] == 40


def test_gc_guard_thresholds_come_from_config(monkeypatch):
    monkeypatch.setattr(config, "get_gc_max_orphans", lambda: 1)
    result, gs, _ = _gc(monkeypatch, live=_KEEP, graph=[("u", "a"), ("u", "b")])
    assert result["refused"] is True


def test_gc_refuses_everything_when_live_set_is_empty(monkeypatch):
    """A wrong or truncated entities read must not wipe shared nodes either."""
    graph = [_scan_row("u", "sh1", label="Entity"), ("u", "p1")]
    result, gs, _ = _gc(monkeypatch, live=[], graph=graph)
    assert result["refused"] is True and result["deleted_ok"] == 0
    assert gs.ran(_GC_DELETE) == []
    forced, gs, _ = _gc(monkeypatch, live=[], graph=graph, force=True)
    assert forced["deleted_ok"] == 2


def test_gc_counts_only_what_was_really_deleted(monkeypatch):
    ms = _MS({"FROM entities": [{"user_id": "keeper", "entity_id": "k0"}]})
    gs = _DeletingGS({_GC_SCAN: [_scan_row("u", "a"), _scan_row("u", "b")]}, matched=1)
    _wire(monkeypatch, ms, gs)
    result = reconcile.garbage_collect_orphan_nodes()
    assert result["deleted_ok"] == 1 and result["deleted_failed"] == 1


def test_gc_dry_run_counts_without_deleting(monkeypatch):
    result, gs, _ = _gc(
        monkeypatch, live=_KEEP, graph=[("u", "a"), ("u", "b", "Organisation")], dry_run=True
    )
    assert result["dry_run"] is True and result["orphans_found"] == 2
    assert result["orphans_by_label"] == {"Person": 1, "Organisation": 1}
    assert result["deleted_ok"] == 0 and gs.ran(_GC_DELETE) == []


def test_run_reconciliation_surfaces_a_refused_gc(monkeypatch):
    for name in (
        "reconcile_documents",
        "reconcile_entities",
        "reconcile_sessions",
        "reconcile_notes",
        "reconcile_audio",
    ):
        monkeypatch.setattr(
            reconcile,
            name,
            lambda limit=None: {
                "scanned": 0,
                "projected_ok": 0,
                "projected_failed": 0,
                "stamped": 0,
            },
        )
    monkeypatch.setattr(reconcile, "reconcile_shared_entities", lambda limit=None: {})
    monkeypatch.setattr(reconcile, "reconcile_entity_edges", lambda limit=None: {})
    monkeypatch.setattr(
        reconcile,
        "garbage_collect_orphan_nodes",
        lambda limit=None: {"deleted_ok": 0, "refused": True},
    )
    monkeypatch.setattr(reconcile, "temporal_feature_enabled", lambda: False)
    totals = reconcile.run_reconciliation()["totals"]
    assert totals["orphan_gc_refused"] is True and totals["orphans_deleted"] == 0


# ---------------------------------------------------------------------------
# recount_cooccurrence (LUM-952)
# ---------------------------------------------------------------------------

_U1 = "11111111-1111-1111-1111-111111111111"
_U2 = "22222222-2222-2222-2222-222222222222"
_U3 = "33333333-3333-3333-3333-333333333333"


def _recount(monkeypatch, edges, counted, *, dry_run=False):
    ms = _MS({"FROM unnest(": counted})
    gs = _GS({"RETURN a.lumogis_id AS x": edges})
    _wire(monkeypatch, ms, gs)
    return reconcile.recount_cooccurrence(user_id="alice", dry_run=dry_run), gs


def test_recount_sets_distinct_shared_evidence_and_skips_zero(monkeypatch):
    result, gs = _recount(
        monkeypatch,
        edges=[{"x": _U1, "y": _U2, "c": 9}, {"x": _U1, "y": _U3, "c": 4}],
        counted=[{"x": _U1, "y": _U2, "n": 2}],  # _U1/_U3 share nothing now
    )
    writes = gs.ran("SET r.co_occurrence_count = $n")
    assert [(w["x"], w["y"], w["n"]) for w in writes] == [(_U1, _U2, 2)]
    assert result["edges_changed"] == 1 and result["zero_skipped"] == 1


def test_recount_only_reads_edges_that_carry_a_count(monkeypatch):
    _, gs = _recount(monkeypatch, edges=[], counted=[])
    scan = next(c for c, _ in gs.queries if "RETURN a.lumogis_id AS x" in c)
    assert "r.co_occurrence_count IS NOT NULL" in scan  # typed RELATES_TO edges have none


def test_recount_write_carries_label_and_scope_predicates(monkeypatch):
    _, gs = _recount(
        monkeypatch, edges=[{"x": _U1, "y": _U2, "c": 9}], counted=[{"x": _U1, "y": _U2, "n": 2}]
    )
    write = next(c for c, _ in gs.queries if "SET r.co_occurrence_count = $n" in c)
    assert "a:Person" in write and "b:Concept" in write
    # Absent or 'personal' scope both count as personal (LUM-961 stamps 'personal').
    assert "coalesce(a.scope, 'personal') = 'personal'" in write
    assert "a:Entity" not in write


def test_recount_skips_non_uuid_ids_and_dry_run_writes_nothing(monkeypatch):
    result, gs = _recount(
        monkeypatch,
        edges=[{"x": "legacy-id", "y": _U2, "c": 3}, {"x": _U1, "y": _U2, "c": 9}],
        counted=[{"x": _U1, "y": _U2, "n": 2}],
        dry_run=True,
    )
    assert result["skipped"] == 1 and result["edges_changed"] == 1
    assert gs.ran("SET r.co_occurrence_count = $n") == []


# ---------------------------------------------------------------------------
# backfill_temporal_properties — error paths and batching
# ---------------------------------------------------------------------------

_TS = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")


def test_backfill_valid_at_skips_an_unparseable_candidate():
    assert reconcile._normalised_backfill_valid_at("not-a-date", "2026-01-01T00:00:00Z") == (
        "2026-01-01T00:00:00Z"
    )
    assert _TS.fullmatch(reconcile._normalised_backfill_valid_at("junk", "also junk"))


def test_backfill_is_a_noop_without_a_graph(monkeypatch):
    monkeypatch.setattr(config, "get_graph_store", lambda bank=None: None)
    result = reconcile.backfill_temporal_properties()
    assert result["edges_scanned"] == 0 and result["evidence_stamped"] == 0


def test_backfill_read_failure_on_one_edge_type_does_not_stop_the_others():
    gs = _GS(
        {"[r:MENTIONS]": [{"rid": 1, "last_seen": None, "ts": "2026-01-01T00:00:00Z"}]},
        fail_on=("[r:RELATES_TO]",),
    )

    result = reconcile.backfill_temporal_properties(gs=gs, batch_size=10)

    assert result["edges_scanned"] == 1 and result["edges_stamped"] == 1


def test_backfill_write_failure_is_counted_and_terminates():
    """A failing write leaves rows unstamped, so the read keeps returning them;
    the seen-ids guard must end the loop instead of spinning."""
    rows = [{"rid": i, "last_seen": None, "ts": None} for i in range(3)]
    gs = _GS({"[r:RELATES_TO]": rows}, fail_on=("WHERE id(r) = $rid",))

    result = reconcile.backfill_temporal_properties(gs=gs, batch_size=3)

    assert result["edges_scanned"] == 3
    assert result["edges_failed"] == 3 and result["edges_stamped"] == 0


def test_backfill_evidence_stamp_batches_until_short_and_survives_a_label_failure():
    class _Batches(_GS):
        def __init__(self):
            super().__init__(fail_on=("MATCH (n:Note)",))
            self.doc_batches = [2, 2, 1]  # full, full, short -> three round trips

        def query(self, cypher, params=None):
            if "MATCH (n:Document)" in cypher:
                self.queries.append((cypher, params or {}))
                return [{"cnt": self.doc_batches.pop(0)}]
            return super().query(cypher, params)

    gs = _Batches()

    result = reconcile.backfill_temporal_properties(gs=gs, batch_size=2)

    assert result["evidence_stamped"] == 5
    assert len(gs.ran("MATCH (n:Document)")) == 3
    # The Note failure broke only its own label: the remaining labels still ran.
    assert gs.ran("MATCH (n:Session)") and gs.ran("MATCH (n:AudioMemo)")


# ---------------------------------------------------------------------------
# run_reconciliation — temporal flag and empty input
# ---------------------------------------------------------------------------


def test_run_reconciliation_includes_temporal_backfill_only_when_flag_on(monkeypatch):
    from graph.temporal import TEMPORAL_FLAG_ENV

    ms, gs = _MS(), _GS()
    _wire(monkeypatch, ms, gs)

    monkeypatch.delenv(TEMPORAL_FLAG_ENV, raising=False)
    assert "temporal_backfill" not in reconcile.run_reconciliation()

    monkeypatch.setenv(TEMPORAL_FLAG_ENV, "true")
    assert "temporal_backfill" in reconcile.run_reconciliation()


def test_run_reconciliation_on_empty_stores_is_all_zero_and_records_completion(monkeypatch):
    ms = _MS()
    _wire(monkeypatch, ms, _GS())

    result = reconcile.run_reconciliation()

    totals = result["totals"]
    assert (
        totals["scanned"],
        totals["projected_ok"],
        totals["projected_failed"],
        totals["stamped"],
    ) == (0, 0, 0, 0)
    assert totals["orphans_deleted"] == 0
    assert ms.updates("_job_last_reconciliation")
