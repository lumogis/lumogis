# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""In-place entity relabel on a kind correction (LUM-967).

Hermetic: a recording graph fake and a metadata-store fake that answers the
writer's row read. The relabel against a real FalkorDB (same ``id(n)``, edges
kept, the duplicate the converge prevents) is
``orchestrator/tests/integration/test_graph_entity_relabel_falkordb_live.py``.
"""

from __future__ import annotations

import threading
from datetime import datetime
from datetime import timezone

import pytest
from graph import reconcile
from graph import writer
from graph.schema import NodeLabel

import config

E1 = "11111111-1111-4111-9111-111111111111"
USER = "alice"
NOW = datetime(2026, 11, 16, tzinfo=timezone.utc)


class _GS:
    """Records graph calls in order; ``create_node`` can run a hook (to interleave threads)."""

    def __init__(self, fail_query: bool = False):
        self.calls: list[tuple] = []
        self.fail_query = fail_query
        self.on_create = None

    def create_node(self, labels, properties):
        self.calls.append(("create_node", list(labels), dict(properties)))
        if self.on_create is not None:
            self.on_create()
        return f"n:{properties['lumogis_id']}"

    def create_edge(self, **kw):
        self.calls.append(("create_edge", kw))

    def query(self, cypher, params=None):
        if self.fail_query:
            raise RuntimeError("simulated graph failure")
        self.calls.append(("query", cypher, params))
        return []

    def queries(self):
        return [c for c in self.calls if c[0] == "query"]


class _MS:
    """``fetch_one`` answers the writer's row read with ``row`` (or raises)."""

    def __init__(self, row=None, *, fail_read: bool = False):
        self.row = row
        self.fail_read = fail_read
        self.executed: list[tuple[str, tuple | None]] = []
        self.reads = 0
        self.log: list[str] = []

    def fetch_one(self, sql, params=None):
        self.reads += 1
        self.log.append("read")
        if self.fail_read:
            raise RuntimeError("simulated read failure")
        return dict(self.row) if self.row is not None else None

    def fetch_all(self, sql, params=None):
        return []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))


def _row(kind="ORG", projected=None):
    return {
        "memory_type": None,
        "last_verified_at": None,
        "entity_type": kind,
        "graph_projected_at": projected,
    }


def _project(gs, ms, *, entity_type="PERSON", entity_id=E1, **kw):
    writer.project_entity(
        gs,
        entity_id=entity_id,
        entity_type=entity_type,
        name="Meier Pharmacy",
        evidence_id="doc-1",
        evidence_type="DOCUMENT",
        user_id=USER,
        ms=ms,
        **kw,
    )


@pytest.fixture(autouse=True)
def _no_source_node(monkeypatch):
    monkeypatch.setattr(writer, "_ensure_source_node", lambda *a, **k: None)
    monkeypatch.setattr(writer, "_update_cooccurrence_edges", lambda *a, **k: None)


# ── labels ───────────────────────────────────────────────────────────────────


def test_entity_labels_are_unique_map_values():
    assert NodeLabel.ENTITY_LABELS == ("Person", "Organisation", "Project", "Concept")
    assert set(NodeLabel.ENTITY_LABELS) == set(NodeLabel.ENTITY_TYPE_MAP.values())


def test_gc_labels_unchanged():
    assert reconcile._GC_LABELS == ("Entity", "Person", "Organisation", "Project", "Concept")


# ── converge ─────────────────────────────────────────────────────────────────


def test_project_entity_converges_before_merge():
    gs, ms = _GS(), _MS(_row("ORG"))
    _project(gs, ms, entity_type="ORG")
    kinds = [c[0] for c in gs.calls]
    assert kinds == ["query", "query", "query", "create_node"]
    expected = [
        "MATCH (n:{o}) WHERE n.lumogis_id = $lid AND n.user_id = $uid "
        "REMOVE n:{o} SET n:Organisation".format(o=other)
        for other in ("Person", "Project", "Concept")
    ]
    assert [c[1] for c in gs.queries()] == expected
    assert gs.calls[-1][1] == ["Organisation"]


def test_converge_params_not_interpolated():
    evil = "x' }) DETACH DELETE n //"
    gs = _GS()
    writer._converge_entity_label(gs, entity_id=evil, user_id=USER, label="Organisation")
    assert len(gs.queries()) == 3
    for _, cypher, params in gs.queries():
        assert evil not in cypher
        assert params == {"lid": evil, "uid": USER}


def test_converge_refuses_a_non_entity_label():
    with pytest.raises(ValueError, match="not an entity label"):
        writer._converge_entity_label(_GS(), entity_id=E1, user_id=USER, label="Document")


def test_projected_row_skips_converge_and_no_row_skips_converge():
    gs = _GS()
    _project(gs, _MS(_row("ORG", projected=NOW)), entity_type="ORG")
    assert gs.queries() == []
    assert [c[0] for c in gs.calls] == ["create_node"]

    gs = _GS()
    _project(gs, _MS(None), entity_type="ORG")
    assert gs.queries() == []
    assert gs.calls[-1][1] == ["Organisation"]


def test_row_read_failure_raises_before_graph_write():
    gs, ms = _GS(), _MS(fail_read=True)
    with pytest.raises(RuntimeError, match="simulated read failure"):
        _project(gs, ms)
    assert gs.calls == []
    assert ms.executed == []  # no stamp


def test_stamp_is_conditional_on_projected_kind():
    ms = _MS(_row("ORG"))
    _project(_GS(), ms, entity_type="PERSON")
    ((sql, params),) = ms.executed
    assert sql.endswith("AND entity_type IS NOT DISTINCT FROM %s")
    assert params == (E1, USER, "ORG")  # the stored kind used for the label

    ms = _MS(None)
    _project(_GS(), ms, entity_type="PERSON")
    assert ms.executed[0][1] == (E1, USER, "PERSON")  # the event's kind when there was no row


def test_stamp_also_requires_the_version_that_was_read():
    """Change-and-change-back (ABA): the kind matches again, the version does not."""
    ms = _MS({**_row("PERSON"), "version": 7})
    _project(_GS(), ms, entity_type="PERSON")
    ((sql, params),) = ms.executed
    assert sql.endswith("AND entity_type IS NOT DISTINCT FROM %s AND version = %s")
    assert params == (E1, USER, "PERSON", 7)


def test_stamp_for_other_tables_is_unchanged():
    ms = _MS()
    writer._stamp_graph_projected_at("notes", "note_id", "n1", USER, ms=ms)
    ((sql, params),) = ms.executed
    assert "entity_type" not in sql
    assert params == ("n1", USER)
    # the guard is for ``entities`` only, even if passed for another table
    ms = _MS()
    writer._stamp_graph_projected_at("notes", "note_id", "n1", USER, ms=ms, expect_entity_type="X")
    assert "entity_type" not in ms.executed[0][0]


def test_unknown_type_converges_to_concept():
    gs = _GS()
    _project(gs, _MS(_row("CODING_DECISION")), entity_type="CODING_DECISION")
    assert [c[1].rsplit("SET n:", 1)[1] for c in gs.queries()] == ["Concept"] * 3
    assert [c[1].split("(n:")[1].split(")")[0] for c in gs.queries()] == [
        "Person",
        "Organisation",
        "Project",
    ]
    assert gs.calls[-1][1] == ["Concept"]


def test_staged_entity_skips_converge_and_node():
    gs, ms = _GS(), _MS(_row("ORG"))
    _project(gs, ms, is_staged=True)
    assert gs.calls == [] and ms.reads == 0


def test_converge_failure_propagates_and_does_not_stamp():
    gs, ms = _GS(fail_query=True), _MS(_row("ORG"))
    with pytest.raises(RuntimeError, match="simulated graph failure"):
        _project(gs, ms, entity_type="ORG")
    assert ms.executed == []
    assert [c for c in gs.calls if c[0] == "create_node"] == []


def test_stored_kind_overrides_stale_event_kind():
    gs, ms = _GS(), _MS(_row("ORG"))
    _project(gs, ms, entity_type="PERSON")  # a late event carrying the pre-correction kind
    assert gs.queries() and gs.queries()[0][1].endswith("SET n:Organisation")
    create = gs.calls[-1]
    assert create[1] == ["Organisation"]
    assert create[2]["entity_type"] == "ORG"


def test_no_row_falls_back_to_event_kind():
    gs = _GS()
    _project(gs, _MS(None), entity_type="PROJECT")
    assert gs.calls[-1][1] == ["Project"]
    assert gs.calls[-1][2]["entity_type"] == "PROJECT"


def test_empty_stored_kind_falls_back_to_event_kind():
    gs = _GS()
    _project(gs, _MS(_row(None)), entity_type="PROJECT")
    assert gs.calls[-1][1] == ["Project"]


def test_converge_label_false_skips_converge():
    gs = _GS()
    _project(gs, _MS(_row("ORG")), entity_type="ORG", converge_label=False)
    assert gs.queries() == []
    assert gs.calls[-1][0] == "create_node"


# ── per-entity lock ──────────────────────────────────────────────────────────


def _ids_on_different_stripes():
    base = writer._entity_projection_lock(USER, "e-0")
    for i in range(1, 500):
        if writer._entity_projection_lock(USER, f"e-{i}") is not base:
            return "e-0", f"e-{i}"
    raise AssertionError("no second stripe found")  # pragma: no cover


def test_projection_lock_serialises_same_entity_but_not_others():
    a_id, b_id = _ids_on_different_stripes()
    log: list[str] = []
    gate_a_in_create = threading.Event()
    release_a = threading.Event()

    class _LoggingMS(_MS):
        def fetch_one(self, sql, params=None):
            log.append(f"read:{params[0]}:{threading.current_thread().name}")
            return super().fetch_one(sql, params)

    def _run(entity_id, name, gs):
        _project(gs, _LoggingMS(_row("ORG")), entity_type="ORG", entity_id=entity_id)
        log.append(f"done:{name}")

    gs_a1, gs_a2, gs_b = _GS(), _GS(), _GS()

    def _a1_blocks():
        gate_a_in_create.set()
        assert release_a.wait(5)

    gs_a1.on_create = _a1_blocks
    t1 = threading.Thread(target=_run, args=(a_id, "a1", gs_a1), name="a1")
    t1.start()
    assert gate_a_in_create.wait(5)  # a1 holds the lock, inside create_node

    t2 = threading.Thread(target=_run, args=(a_id, "a2", gs_a2), name="a2")
    t2.start()
    t3 = threading.Thread(target=_run, args=(b_id, "b", gs_b), name="b")
    t3.start()
    t3.join(5)  # another stripe proceeds while a1 holds its lock
    assert "done:b" in log
    t2.join(0.3)
    assert t2.is_alive()  # a2 is waiting on a1's lock, has not read the row yet
    assert not any(e.endswith(":a2") for e in log)

    release_a.set()
    t1.join(5)
    t2.join(5)
    assert not t1.is_alive() and not t2.is_alive()
    reads = [e for e in log if e.startswith("read:")]
    a_reads = [e for e in reads if e.startswith(f"read:{a_id}")]
    assert a_reads == [f"read:{a_id}:a1", f"read:{a_id}:a2"]
    assert log.index(f"read:{a_id}:a2") > log.index("done:b")


# ── reconcile ────────────────────────────────────────────────────────────────


def test_reconcile_entity_arm_converges_once_per_entity(monkeypatch):
    class _World(_MS):
        def fetch_one(self, sql, params=None):
            # A kind change is not a merge: no entity_merges row names this entry,
            # so the LUM-965 prune (gated on one) issues no Cypher.
            if "FROM entity_merges" in sql:
                return None
            return super().fetch_one(sql, params)

        def fetch_all(self, sql, params=None):
            if "FROM entities WHERE" in sql:
                return [
                    {
                        "entity_id": E1,
                        "name": "Meier Pharmacy",
                        "entity_type": "ORG",
                        "user_id": USER,
                    }
                ]
            if "FROM entity_relations" in sql and "WHERE source_id" in sql:
                return [{"evidence_id": f"ev{i}", "evidence_type": "DOCUMENT"} for i in range(3)]
            return []

    ms, gs = _World(_row("ORG")), _GS()
    monkeypatch.setattr(config, "get_metadata_store", lambda: ms)
    monkeypatch.setattr(config, "get_graph_store", lambda bank=None: gs)
    reconcile.reconcile_entities()
    # The row stays stale in this fake (its stamp is a no-op), so every call sees
    # ``graph_projected_at IS NULL``; only ``converge_label=(idx == 0)`` limits it.
    assert len(gs.queries()) == 3  # one converge (3 statements), not 3 × 3
    assert len([c for c in gs.calls if c[0] == "create_node"]) == 3
