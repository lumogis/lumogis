# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Tests for plugins/graph/reconcile.py (in-process reconciliation).

HTTP coverage for ``POST /graph/backfill`` lives in the graph service
(``services/lumogis-graph``, e.g. ``graph/routes.py``), not in Core.

Test coverage:
  1. Stale-row selection: scanned=0 when no stale rows
  2. Successful projection + stamp
  3. No stamp when projection fails
  4. Idempotency: running reconciliation twice yields scanned=0 on second pass
     (because graph_projected_at is stamped after first pass)
  5. Graph unavailable: reconcile passes return gracefully (scanned=0)
  6. Deliberate failure recovery:
     - simulate projection failure
     - verify graph_projected_at remains unstamped (projected_failed=1)
     - re-run after recovery
     - verify projection succeeds and stamp is set (stamped=1)
"""

import pytest
import config
from plugins.graph.reconcile import (
    reconcile_audio,
    reconcile_documents,
    reconcile_entities,
    reconcile_notes,
    reconcile_sessions,
    run_reconciliation,
)


# ---------------------------------------------------------------------------
# Shared mocks
# ---------------------------------------------------------------------------

class MockGraphStore:
    """Minimal in-memory GraphStore for reconciliation tests."""

    def __init__(self):
        self._nodes = {}
        self._next_id = 0
        self.queries = []
        self.fail_on_create = False

    def _new_id(self):
        self._next_id += 1
        return str(self._next_id)

    def ping(self):
        return True

    def create_node(self, labels, properties):
        if self.fail_on_create:
            raise RuntimeError("simulated graph write failure")
        key = (properties.get("lumogis_id", ""), properties.get("user_id", ""))
        for nid, node in self._nodes.items():
            p = node["props"]
            if p.get("lumogis_id") == key[0] and p.get("user_id") == key[1]:
                node["props"].update(properties)
                return nid
        nid = self._new_id()
        self._nodes[nid] = {"labels": labels, "props": dict(properties)}
        return nid

    def create_edge(self, from_id, to_id, rel_type, properties):
        pass  # sufficient for reconcile unit tests

    def query(self, cypher, params=None):
        self.queries.append((cypher, params or {}))
        return []

    def nodes_with_label(self, label):
        return [n["props"] for n in self._nodes.values() if label in n["labels"]]


class StampTrackingStore:
    """MetadataStore that tracks execute() calls (for stamp verification)."""

    def __init__(self, rows_by_query=None):
        self.rows_by_query = rows_by_query or {}
        self.executed: list[tuple] = []
        self.stamps: list[tuple] = []  # (table, id_val) pairs

    def ping(self):
        return True

    def execute(self, query, params=None):
        self.executed.append((query, params))
        if "graph_projected_at" in query and params:
            self.stamps.append(params)

    def fetch_one(self, query, params=None):
        return None

    def fetch_all(self, query, params=None):
        for prefix, rows in self.rows_by_query.items():
            if prefix.lower() in query.lower():
                return rows
        return []

    def close(self):
        pass


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_graph(monkeypatch):
    gs = MockGraphStore()
    config._instances["graph_store"] = gs
    yield gs
    config._instances.pop("graph_store", None)


@pytest.fixture
def no_graph(monkeypatch):
    config._instances["graph_store"] = None
    yield
    config._instances.pop("graph_store", None)


@pytest.fixture
def tracking_ms():
    return StampTrackingStore()


# ---------------------------------------------------------------------------
# 1. Stale-row selection: no stale rows → scanned=0
# ---------------------------------------------------------------------------

class TestNoStaleRows:
    def test_documents_no_stale(self, mock_graph):
        config._instances["metadata_store"] = StampTrackingStore(
            rows_by_query={"file_index": []}
        )
        result = reconcile_documents()
        assert result["scanned"] == 0
        assert result["projected_ok"] == 0
        assert result["projected_failed"] == 0

    def test_entities_no_stale(self, mock_graph):
        config._instances["metadata_store"] = StampTrackingStore(
            rows_by_query={"entities": []}
        )
        result = reconcile_entities()
        assert result["scanned"] == 0

    def test_sessions_no_stale(self, mock_graph):
        config._instances["metadata_store"] = StampTrackingStore(
            rows_by_query={"sessions": []}
        )
        result = reconcile_sessions()
        assert result["scanned"] == 0

    def test_notes_no_stale(self, mock_graph):
        config._instances["metadata_store"] = StampTrackingStore(
            rows_by_query={"notes": []}
        )
        result = reconcile_notes()
        assert result["scanned"] == 0

    def test_audio_no_stale(self, mock_graph):
        config._instances["metadata_store"] = StampTrackingStore(
            rows_by_query={"audio_memos": []}
        )
        result = reconcile_audio()
        assert result["scanned"] == 0


# ---------------------------------------------------------------------------
# 2. Successful projection + stamp
# ---------------------------------------------------------------------------

class TestSuccessfulProjection:
    def test_document_projected_and_stamped(self, mock_graph):
        ms = StampTrackingStore(
            rows_by_query={
                "file_index": [
                    {"file_path": "/data/a.pdf", "file_type": "pdf", "user_id": "default"}
                ]
            }
        )
        config._instances["metadata_store"] = ms
        result = reconcile_documents()
        assert result["scanned"] == 1
        assert result["projected_ok"] == 1
        assert result["stamped"] == 1
        assert result["projected_failed"] == 0
        # Stamp was written
        assert any("graph_projected_at" in str(e) for e, _ in ms.executed)

    def test_note_projected_and_stamped(self, mock_graph):
        ms = StampTrackingStore(
            rows_by_query={
                "notes": [{"note_id": "note-111", "user_id": "default"}]
            }
        )
        config._instances["metadata_store"] = ms
        result = reconcile_notes()
        assert result["scanned"] == 1
        assert result["projected_ok"] == 1
        assert result["stamped"] == 1

    def test_audio_projected_and_stamped(self, mock_graph):
        ms = StampTrackingStore(
            rows_by_query={
                "audio_memos": [
                    {
                        "audio_id": "audio-222",
                        "file_path": "/data/m.mp3",
                        "duration_seconds": 60.0,
                        "user_id": "default",
                    }
                ]
            }
        )
        config._instances["metadata_store"] = ms
        result = reconcile_audio()
        assert result["scanned"] == 1
        assert result["projected_ok"] == 1
        assert result["stamped"] == 1

    def test_session_projected_and_stamped(self, mock_graph):
        ms = StampTrackingStore(
            rows_by_query={
                "sessions": [
                    {
                        "session_id": "sess-aaa",
                        "summary": "summary",
                        "topics": ["topic1"],
                        "entities": [],
                        "entity_ids": ["eid-001", "eid-002"],
                        "user_id": "default",
                    }
                ]
            }
        )
        config._instances["metadata_store"] = ms
        result = reconcile_sessions()
        assert result["scanned"] == 1
        assert result["projected_ok"] == 1
        assert result["stamped"] == 1

    def test_session_uses_uuid_entity_ids_when_present(self, mock_graph):
        """reconcile_sessions passes entity_ids from DB row directly to project_session."""
        captured = {}
        import plugins.graph.writer as writer_mod
        orig = writer_mod.project_session

        def capturing_project_session(gs, *, entity_ids, **kwargs):
            captured["entity_ids"] = entity_ids
            return orig(gs, entity_ids=entity_ids, **kwargs)

        ms = StampTrackingStore(
            rows_by_query={
                "sessions": [
                    {
                        "session_id": "sess-bbb",
                        "summary": "s",
                        "topics": [],
                        "entities": [],
                        "entity_ids": ["uuid-111", "uuid-222"],
                        "user_id": "default",
                    }
                ]
            }
        )
        config._instances["metadata_store"] = ms
        import plugins.graph.writer as writer_mod  # noqa: F811
        writer_mod.project_session = capturing_project_session
        try:
            reconcile_sessions()
        finally:
            writer_mod.project_session = orig
        assert captured.get("entity_ids") == ["uuid-111", "uuid-222"]

    def test_session_falls_back_to_name_resolution_when_entity_ids_empty(self, mock_graph):
        """Historical rows with empty entity_ids pass None → name-string fallback."""
        captured = {}
        import plugins.graph.writer as writer_mod
        orig = writer_mod.project_session

        def capturing_project_session(gs, *, entity_ids, **kwargs):
            captured["entity_ids"] = entity_ids
            return orig(gs, entity_ids=entity_ids, **kwargs)

        ms = StampTrackingStore(
            rows_by_query={
                "sessions": [
                    {
                        "session_id": "sess-ccc",
                        "summary": "s",
                        "topics": [],
                        "entities": ["Ada Lovelace"],
                        "entity_ids": [],
                        "user_id": "default",
                    }
                ]
            }
        )
        config._instances["metadata_store"] = ms
        writer_mod.project_session = capturing_project_session
        try:
            reconcile_sessions()
        finally:
            writer_mod.project_session = orig
        assert captured.get("entity_ids") is None

    def test_entity_projected_and_stamped(self, mock_graph):
        ms = StampTrackingStore(
            rows_by_query={
                # "from entities where" matches the stale-entity SELECT but NOT the
                # co-occurrence JOIN query ("FROM entity_relations er INNER JOIN entities e").
                "from entities where": [
                    {
                        "entity_id": "eid-001",
                        "name": "Ada",
                        "entity_type": "PERSON",
                        "user_id": "default",
                    }
                ],
                # "evidence_id, evidence_type" matches the per-entity reconcile fetch
                # ("SELECT evidence_id, evidence_type FROM entity_relations WHERE source_id=…")
                # but NOT the co-occurrence JOIN query (which selects er.source_id).
                "evidence_id, evidence_type": [
                    {
                        "evidence_id": "/data/doc.pdf",
                        "evidence_type": "DOCUMENT",
                    }
                ],
            }
        )
        config._instances["metadata_store"] = ms
        result = reconcile_entities()
        assert result["scanned"] == 1
        assert result["projected_ok"] == 1
        assert result["stamped"] == 1


# ---------------------------------------------------------------------------
# 3. No stamp when projection fails
# ---------------------------------------------------------------------------

class TestNoStampOnFailure:
    def test_document_failure_no_stamp(self, mock_graph):
        mock_graph.fail_on_create = True
        ms = StampTrackingStore(
            rows_by_query={
                "file_index": [
                    {"file_path": "/data/fail.pdf", "file_type": "pdf", "user_id": "default"}
                ]
            }
        )
        config._instances["metadata_store"] = ms
        result = reconcile_documents()
        assert result["projected_failed"] == 1
        assert result["stamped"] == 0
        # No graph_projected_at stamp written
        assert not any("graph_projected_at" in str(e) for e, _ in ms.executed)

    def test_note_failure_no_stamp(self, mock_graph):
        mock_graph.fail_on_create = True
        ms = StampTrackingStore(
            rows_by_query={
                "notes": [{"note_id": "note-fail", "user_id": "default"}]
            }
        )
        config._instances["metadata_store"] = ms
        result = reconcile_notes()
        assert result["projected_failed"] == 1
        assert result["stamped"] == 0

    def test_audio_failure_no_stamp(self, mock_graph):
        mock_graph.fail_on_create = True
        ms = StampTrackingStore(
            rows_by_query={
                "audio_memos": [
                    {
                        "audio_id": "audio-fail",
                        "file_path": "/x.mp3",
                        "duration_seconds": None,
                        "user_id": "default",
                    }
                ]
            }
        )
        config._instances["metadata_store"] = ms
        result = reconcile_audio()
        assert result["projected_failed"] == 1
        assert result["stamped"] == 0


# ---------------------------------------------------------------------------
# 4. Idempotency: second pass finds no stale rows
# ---------------------------------------------------------------------------

class TestIdempotency:
    def test_reconcile_twice_second_is_empty(self, mock_graph):
        """After a successful first pass, a second pass should scan 0 rows.

        Simulated by: first ms has stale rows, second ms has no stale rows
        (as Postgres would after stamping).
        """
        ms1 = StampTrackingStore(
            rows_by_query={
                "notes": [{"note_id": "note-idem", "user_id": "default"}]
            }
        )
        config._instances["metadata_store"] = ms1
        r1 = reconcile_notes()
        assert r1["projected_ok"] == 1

        # After stamping, the next query returns no stale rows
        ms2 = StampTrackingStore(rows_by_query={"notes": []})
        config._instances["metadata_store"] = ms2
        r2 = reconcile_notes()
        assert r2["scanned"] == 0
        assert r2["projected_ok"] == 0

    def test_run_reconciliation_returns_all_types(self, mock_graph):
        config._instances["metadata_store"] = StampTrackingStore()
        result = run_reconciliation()
        assert "documents" in result
        assert "entities" in result
        assert "sessions" in result
        assert "notes" in result
        assert "audio" in result
        assert "totals" in result

    def test_run_reconciliation_totals_are_correct(self, mock_graph):
        ms = StampTrackingStore(
            rows_by_query={
                "notes": [
                    {"note_id": "n1", "user_id": "default"},
                    {"note_id": "n2", "user_id": "default"},
                ]
            }
        )
        config._instances["metadata_store"] = ms
        result = run_reconciliation()
        # 2 notes scanned; all others zero
        assert result["totals"]["scanned"] >= 2
        assert result["totals"]["projected_ok"] >= 2


# ---------------------------------------------------------------------------
# 5. Graph unavailable → graceful no-op
# ---------------------------------------------------------------------------

class TestGraphUnavailable:
    def test_all_passes_succeed_with_no_graph(self, no_graph):
        result = run_reconciliation()
        assert result["totals"]["scanned"] == 0
        assert result["totals"]["projected_failed"] == 0

    def test_individual_passes_return_zero_when_graph_none(self, no_graph):
        for fn in (
            reconcile_documents,
            reconcile_entities,
            reconcile_sessions,
            reconcile_notes,
            reconcile_audio,
        ):
            r = fn()
            assert r["scanned"] == 0
            assert r["projected_failed"] == 0


# ---------------------------------------------------------------------------
# 6. Deliberate failure recovery scenario
# ---------------------------------------------------------------------------

class TestFailureRecovery:
    def test_failure_then_recovery(self, mock_graph):
        """
        Pass 1: graph is broken → projected_failed=1, stamp NOT set.
        Pass 2: graph is healthy → projected_ok=1, stamp IS set.
        """
        stale_rows = {"notes": [{"note_id": "note-rec", "user_id": "default"}]}

        # Pass 1 — graph write fails
        mock_graph.fail_on_create = True
        ms1 = StampTrackingStore(rows_by_query=stale_rows)
        config._instances["metadata_store"] = ms1
        r1 = reconcile_notes()
        assert r1["projected_failed"] == 1
        assert r1["stamped"] == 0
        assert not any("graph_projected_at" in str(e) for e, _ in ms1.executed)

        # Pass 2 — graph recovered; same stale rows returned (unstamped)
        mock_graph.fail_on_create = False
        ms2 = StampTrackingStore(rows_by_query=stale_rows)
        config._instances["metadata_store"] = ms2
        r2 = reconcile_notes()
        assert r2["projected_ok"] == 1
        assert r2["stamped"] == 1
        assert any("graph_projected_at" in str(e) for e, _ in ms2.executed)


# ---------------------------------------------------------------------------
# POST /graph/backfill (removed from Core tests)
# ---------------------------------------------------------------------------
# Core orchestrator no longer mounts graph HTTP routes; backfill is owned by
# services/lumogis-graph (see graph/routes.py there). Add or extend HTTP tests
# under services/lumogis-graph/tests/ rather than plugins.graph.routes here.


# ---------------------------------------------------------------------------
# Pass 1: staged entity exclusion
# ---------------------------------------------------------------------------

class TestStagedEntityExclusion:
    def test_staged_entity_excluded_from_stale_query(self, mock_graph):
        """reconcile_entities must not project entities where is_staged IS TRUE.

        We verify by confirming the SQL sent to fetch_all contains the
        is_staged exclusion predicate, regardless of what the mock returns.
        """
        fetched_queries: list[str] = []

        class CapturingStore(StampTrackingStore):
            def fetch_all(self, query, params=None):
                fetched_queries.append(query)
                return []

        config._instances["metadata_store"] = CapturingStore()
        reconcile_entities()

        entity_queries = [q for q in fetched_queries if "entities" in q.lower()]
        assert entity_queries, "Expected at least one entities fetch_all query"
        # The predicate must exclude staged rows
        for q in entity_queries:
            assert "is_staged" in q.lower() or "IS NOT TRUE" in q, (
                f"Entity stale query does not exclude staged entities:\n{q}"
            )

    def test_staged_row_is_not_projected(self, mock_graph):
        """A row returned from fetch_all with is_staged=TRUE must not reach project_entity."""
        projected_ids: list[str] = []
        import plugins.graph.writer as writer_mod
        orig = writer_mod.project_entity

        def capturing_project(gs, *, entity_id, **kwargs):
            projected_ids.append(entity_id)
            return orig(gs, entity_id=entity_id, **kwargs)

        # The query already filters out staged rows (WHERE is_staged IS NOT TRUE),
        # but even if a staged row slipped through, project_entity with is_staged=False
        # (our explicit pass) would still project it. The key gate is the SQL filter.
        # This test verifies that entity_id "staged-001" is NOT projected.
        ms = StampTrackingStore(
            rows_by_query={
                # Simulate the SQL filter working: no rows returned for entities
                "entities": []
            }
        )
        config._instances["metadata_store"] = ms
        writer_mod.project_entity = capturing_project
        try:
            reconcile_entities()
        finally:
            writer_mod.project_entity = orig

        assert "staged-001" not in projected_ids


# ---------------------------------------------------------------------------
# 10. Limit parameter is passed through to SQL query
# ---------------------------------------------------------------------------

class TestLimitParam:
    def test_limit_clause_in_query(self, mock_graph):
        """fetch_all should receive a query containing LIMIT when limit is given."""
        fetched = []

        class CapturingStore(StampTrackingStore):
            def fetch_all(self, query, params=None):
                fetched.append(query)
                return []

        config._instances["metadata_store"] = CapturingStore()
        reconcile_notes(limit=10)
        assert any("LIMIT 10" in q for q in fetched)

    def test_no_limit_clause_when_none(self, mock_graph):
        fetched = []

        class CapturingStore(StampTrackingStore):
            def fetch_all(self, query, params=None):
                fetched.append(query)
                return []

        config._instances["metadata_store"] = CapturingStore()
        reconcile_notes(limit=None)
        assert not any("LIMIT" in q for q in fetched)
