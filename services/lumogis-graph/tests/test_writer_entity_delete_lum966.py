# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Graph side of deleting an entry (LUM-966).

Hermetic: a recording graph fake and a metadata-store fake. The delete against a
real FalkorDB is ``orchestrator/tests/integration/test_graph_entity_delete_falkordb_live.py``.
"""

from __future__ import annotations

import pytest
from graph import writer
from graph.schema import NodeLabel

import config

E1 = "11111111-1111-4111-9111-111111111111"
P1 = "22222222-2222-4222-9222-222222222222"
USER = "alice"


class _GS:
    def __init__(self, fail_query: bool = False):
        self.calls: list[tuple] = []
        self.fail_query = fail_query

    def create_node(self, labels, properties):
        self.calls.append(("create_node", list(labels), dict(properties)))
        return "n1"

    def create_edge(self, **kw):
        self.calls.append(("create_edge", kw))

    def query(self, cypher, params=None):
        if self.fail_query:
            raise RuntimeError("simulated graph failure")
        self.calls.append(("query", cypher, params))
        return []


class _MS:
    """Answers the entity row read (``row``) and the tombstone read (``tombstoned``)."""

    def __init__(self, row=None, *, tombstoned=False, tombstone_error: Exception | None = None):
        self.row = row
        self.tombstoned = tombstoned
        self.tombstone_error = tombstone_error
        self.executed: list[tuple[str, tuple | None]] = []

    def fetch_one(self, sql, params=None):
        if "FROM entity_deletions" in sql:
            if self.tombstone_error is not None:
                raise self.tombstone_error
            return {"x": 1} if self.tombstoned else None
        return dict(self.row) if self.row is not None else None

    def fetch_all(self, sql, params=None):
        return []

    def execute(self, sql, params=None):
        self.executed.append((" ".join(sql.split()), params))


@pytest.fixture(autouse=True)
def _no_source_node(monkeypatch):
    monkeypatch.setattr(writer, "_ensure_source_node", lambda *a, **k: None)
    monkeypatch.setattr(writer, "_update_cooccurrence_edges", lambda *a, **k: None)


def _delete(gs, ms, **kw):
    kw.setdefault("projection_ids", [P1])

    def _get_gs():
        return gs

    def _get_ms():
        return ms

    orig = (config.get_graph_store, config.get_metadata_store)
    config.get_graph_store, config.get_metadata_store = _get_gs, _get_ms
    try:
        writer.on_entity_deleted(entity_id=E1, user_id=USER, **kw)
    finally:
        config.get_graph_store, config.get_metadata_store = orig


def _project(gs, ms, entity_type="PERSON"):
    writer.project_entity(
        gs,
        entity_id=E1,
        entity_type=entity_type,
        name="Total",
        evidence_id="doc-1",
        evidence_type="DOCUMENT",
        user_id=USER,
        ms=ms,
    )


# ── G1–G3: the handler ───────────────────────────────────────────────────────


def test_G1_one_label_scoped_delete_per_entity_label_and_one_shared_statement():
    gs, ms = _GS(), _MS()
    _delete(gs, ms)
    cyphers = [c[1] for c in gs.calls if c[0] == "query"]
    assert len(cyphers) == len(NodeLabel.ENTITY_LABELS) + 1
    for label, cypher in zip(NodeLabel.ENTITY_LABELS, cyphers):
        assert cypher.startswith(f"MATCH (n:{label}) WHERE")
        assert "DETACH DELETE n" in cypher
    assert cyphers[-1].startswith("MATCH (n:Entity) WHERE")
    assert all("MATCH (n)" not in c for c in cyphers)  # never an unlabelled scan


def test_G2_ids_travel_as_parameters_never_in_the_cypher():
    gs, ms = _GS(), _MS()
    _delete(gs, ms)
    for call in (c for c in gs.calls if c[0] == "query"):
        assert E1 not in call[1] and P1 not in call[1] and USER not in call[1]
        assert call[2]["lid"] == E1 and call[2]["uid"] == USER
    assert [c for c in gs.calls if c[0] == "query"][-1][2]["pids"] == [P1]


def test_G3_stamps_graph_cleared_after_the_deletes_and_never_raises_on_a_graph_error():
    gs, ms = _GS(), _MS()
    _delete(gs, ms)
    (sql, params) = ms.executed[0]
    assert sql.startswith("UPDATE entity_deletions SET graph_cleared_at = NOW()")
    assert params == (E1, USER)

    gs, ms = _GS(fail_query=True), _MS()
    _delete(gs, ms)  # logged, not raised
    assert ms.executed == []  # unstamped, so the sweep re-fires


def test_G3b_the_graph_is_a_no_op_when_there_is_no_store():
    ms = _MS()
    orig = config.get_graph_store
    config.get_graph_store = lambda: None
    try:
        writer.on_entity_deleted(entity_id=E1, user_id=USER)
    finally:
        config.get_graph_store = orig
    assert ms.executed == []


# ── G4–G5: a late ENTITY_CREATED ─────────────────────────────────────────────


def test_G4_project_entity_with_no_row_and_a_tombstone_writes_nothing():
    gs, ms = _GS(), _MS(row=None, tombstoned=True)
    _project(gs, ms)
    assert gs.calls == []
    assert ms.executed == []  # no graph_projected_at stamp either


def test_G5_project_entity_with_no_row_and_no_tombstone_still_writes_the_fallback():
    gs, ms = _GS(), _MS(row=None, tombstoned=False)
    _project(gs, ms)
    assert [c[0] for c in gs.calls] == ["create_node"]
    assert gs.calls[0][1] == ["Person"]


def test_G5b_a_live_row_never_consults_the_tombstone():
    row = {
        "memory_type": None,
        "last_verified_at": None,
        "entity_type": "ORG",
        "graph_projected_at": None,
        "version": 1,
    }
    gs, ms = _GS(), _MS(row=row, tombstone_error=RuntimeError("must not be read"))
    _project(gs, ms)
    assert gs.calls[-1][0] == "create_node" or any(c[0] == "create_node" for c in gs.calls)


# ── G6: a share queued before the delete ─────────────────────────────────────


def test_G6_on_document_shared_skips_a_tombstoned_source(monkeypatch):
    import projection as shared_projection

    projected: list[str] = []
    monkeypatch.setattr(
        shared_projection,
        "project_entity_into_graph",
        lambda **kw: projected.append(kw["src_entity_id"]) or True,
    )
    monkeypatch.setattr(writer, "_stamp_graph_projected_at", lambda *a, **k: None)

    class _Both(_MS):
        def fetch_one(self, sql, params=None):
            if "FROM entity_deletions" in sql:
                return {"x": 1} if params[0] == E1 else None
            return None

    orig = (config.get_graph_store, config.get_metadata_store)
    config.get_graph_store, config.get_metadata_store = lambda: _GS(), lambda: _Both()
    try:
        writer.on_document_shared(
            file_path="/d.pdf",
            user_id=USER,
            entities=[
                {
                    "src_entity_id": E1,
                    "proj_entity_id": P1,
                    "name": "Gone",
                    "entity_type": "PERSON",
                },
                {
                    "src_entity_id": "other",
                    "proj_entity_id": "p2",
                    "name": "Kept",
                    "entity_type": "ORG",
                },
            ],
        )
    finally:
        config.get_graph_store, config.get_metadata_store = orig
    assert projected == ["other"]


# ── G7: the tombstone read's failure semantics ───────────────────────────────


class _UndefinedTable(Exception):
    pgcode = "42P01"


def test_G7_undefined_table_means_not_deleted():
    ms = _MS(tombstone_error=_UndefinedTable("relation does not exist"))
    assert writer._entity_deleted(ms, E1, USER) is False


def test_G7_any_other_error_fails_closed_and_skips_the_write(caplog):
    ms = _MS(row=None, tombstone_error=RuntimeError("connection reset"))
    assert writer._entity_deleted(ms, E1, USER) is True
    assert "tombstone lookup failed" in caplog.text
    gs = _GS()
    _project(gs, ms)  # the row is missing and the read errors: write nothing
    assert gs.calls == []


# ── G8: Hide for graph chat-context seeds, and the two visibility mirrors agree ──


def test_G8_context_seed_statements_carry_the_hide_clause(monkeypatch):
    from graph import query

    seen: list[tuple[str, tuple]] = []

    class _Store:
        def fetch_all(self, sql, params=None):
            seen.append((" ".join(sql.split()), tuple(params)))
            return []

        def fetch_one(self, sql, params=None):
            seen.append((" ".join(sql.split()), tuple(params)))
            return None

    monkeypatch.setattr(config, "get_metadata_store", lambda: _Store())
    monkeypatch.setattr(config, "get_graph_min_mention_count", lambda: 1)
    query._detect_entities_in_query("what is Total", USER)
    query._fetch_entity_row_for_context(E1, USER)
    seeds = [(s, p) for s, p in seen if "FROM entities" in s]  # not the allows_shared read
    assert len(seeds) == 2
    for sql, params in seeds:
        assert "NOT EXISTS (SELECT 1 FROM entity_hidden h WHERE h.user_id = %s" in sql
        assert params.count(USER) >= 2  # the visibility clause and the hide clause


def test_G8_the_graph_mirror_equals_cores_not_hidden_sql():
    import importlib.util
    import pathlib

    # Needs Core's source next to the graph service: true in the checkout (CI), not in
    # the graph-service image that `make compose-test-kg` runs (LUM-961 pattern).
    here = pathlib.Path(__file__).resolve()
    core_visibility = (
        here.parents[3] / "orchestrator" / "visibility.py" if len(here.parents) > 3 else None
    )
    if core_visibility is None or not core_visibility.exists():
        pytest.skip("orchestrator tree not in this image")
    spec = importlib.util.spec_from_file_location(
        "core_visibility_for_mirror_test", core_visibility
    )
    core = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(core)
    from visibility import not_hidden_sql

    for qualifier in ("", "e", "ent_1"):
        assert not_hidden_sql(USER, qualifier) == core.not_hidden_sql(USER, qualifier)
    with pytest.raises(ValueError):
        not_hidden_sql(USER, "e; DROP TABLE x")


def test_E4_webhook_route_resolves_entity_deleted_to_the_writer_handler():
    """E4: a handler-name typo would be a 500 on every delete in GRAPH_MODE=service."""
    from models.webhook import WebhookEvent
    from routes.webhook import _resolve_handler

    assert _resolve_handler(WebhookEvent.ENTITY_DELETED) is writer.on_entity_deleted
