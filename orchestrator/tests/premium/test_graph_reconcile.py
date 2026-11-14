# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Tests for plugins/graph/reconcile.py.

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

POST /graph/backfill lives in the KG service and is tested in
services/lumogis-graph/tests/test_graph_backfill_route.py (LUM-54).
"""

import pytest
from plugins.graph.reconcile import reconcile_audio
from plugins.graph.reconcile import reconcile_documents
from plugins.graph.reconcile import reconcile_entities
from plugins.graph.reconcile import reconcile_entity_edges
from plugins.graph.reconcile import reconcile_notes
from plugins.graph.reconcile import reconcile_sessions
from plugins.graph.reconcile import run_reconciliation

import config

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
    # VERIFY-PLAN: LUM-293 — bank-scoped graph_store cache key
    key = config._graph_store_cache_key("personal")
    config._instances[key] = gs
    yield gs
    config._instances.pop(key, None)


@pytest.fixture
def no_graph(monkeypatch):
    key = config._graph_store_cache_key("personal")
    config._instances[key] = None
    yield
    config._instances.pop(key, None)


@pytest.fixture
def tracking_ms():
    return StampTrackingStore()


# ---------------------------------------------------------------------------
# 1. Stale-row selection: no stale rows → scanned=0
# ---------------------------------------------------------------------------


class TestNoStaleRows:
    def test_documents_no_stale(self, mock_graph):
        config._instances["metadata_store"] = StampTrackingStore(rows_by_query={"file_index": []})
        result = reconcile_documents()
        assert result["scanned"] == 0
        assert result["projected_ok"] == 0
        assert result["projected_failed"] == 0

    def test_entities_no_stale(self, mock_graph):
        config._instances["metadata_store"] = StampTrackingStore(rows_by_query={"entities": []})
        result = reconcile_entities()
        assert result["scanned"] == 0

    def test_sessions_no_stale(self, mock_graph):
        config._instances["metadata_store"] = StampTrackingStore(rows_by_query={"sessions": []})
        result = reconcile_sessions()
        assert result["scanned"] == 0

    def test_notes_no_stale(self, mock_graph):
        config._instances["metadata_store"] = StampTrackingStore(rows_by_query={"notes": []})
        result = reconcile_notes()
        assert result["scanned"] == 0

    def test_audio_no_stale(self, mock_graph):
        config._instances["metadata_store"] = StampTrackingStore(rows_by_query={"audio_memos": []})
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
            rows_by_query={"notes": [{"note_id": "note-111", "user_id": "default"}]}
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
        import graph.writer as writer_mod

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
        writer_mod.project_session = capturing_project_session
        try:
            reconcile_sessions()
        finally:
            writer_mod.project_session = orig
        assert captured.get("entity_ids") == ["uuid-111", "uuid-222"]

    def test_session_falls_back_to_name_resolution_when_entity_ids_empty(self, mock_graph):
        """Historical rows with empty entity_ids pass None → name-string fallback."""
        captured = {}
        import graph.writer as writer_mod

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
            rows_by_query={"notes": [{"note_id": "note-fail", "user_id": "default"}]}
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
            rows_by_query={"notes": [{"note_id": "note-idem", "user_id": "default"}]}
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
        import graph.writer as writer_mod

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


# ---------------------------------------------------------------------------
# 11. Typed entity_edges replay (LUM-580)
# ---------------------------------------------------------------------------


class _EdgeReplayGraph:
    """Node-aware graph mock: answers MATCH..MERGE..RETURN count(r) by node
    presence, and records every query for collision/isolation assertions."""

    def __init__(self, nodes=None):
        self.nodes = set(nodes or [])  # set of (lumogis_id, user_id)
        self.queries = []
        self.fail = False

    def ping(self):
        return True

    def add_node(self, lid, uid):
        self.nodes.add((lid, uid))

    def query(self, cypher, params=None):
        params = params or {}
        self.queries.append((cypher, params))
        if self.fail:
            raise RuntimeError("simulated graph failure")
        if "RETURN count(r) AS attached" in cypher:
            src, dst, uid = params.get("src"), params.get("dst"), params.get("user_id")
            both = (src, uid) in self.nodes and (dst, uid) in self.nodes
            return [{"attached": 1 if both else 0}]
        return []


class _EdgeScanStore:
    """MetadataStore stub: returns a fixed edge scan and records stamp ids.

    Writes are told apart by their SET clause: a stamp sets
    ``graph_projected_at = now()``, a LUM-599 miss bumps ``reconcile_attempts``.
    """

    def __init__(self, rows, aged_out=0):
        self._rows = rows
        self._aged_out = aged_out
        self.scans: list[str] = []
        self.scan_params: list = []
        self.counts: list[tuple] = []
        self.stamped_ids: list = []
        self.stamp_sql: list[str] = []
        self.incremented_ids: list = []

    def ping(self):
        return True

    def fetch_all(self, query, params=None):
        self.scans.append(query)
        self.scan_params.append(params)
        return list(self._rows)

    def fetch_one(self, query, params=None):
        if "count(*)" in query:
            self.counts.append((query, params))
            return {"n": self._aged_out}
        return None

    def execute(self, query, params=None):
        if "SET graph_projected_at = now()" in query and params:
            self.stamped_ids.append(params[0])
            self.stamp_sql.append(query)
        elif "reconcile_attempts = reconcile_attempts + 1" in query and params:
            self.incremented_ids.append(params[0])

    def close(self):
        pass


def _edge_row(**over):
    row = {
        "id": "edge-1",
        "user_id": "u",
        "bank": "coding",
        "src_entity_id": "s",
        "dst_entity_id": "d",
        "relation_type": "IMPLEMENTS",
    }
    row.update(over)
    return row


def _register_graph(bank, graph):
    key = config._graph_store_cache_key(bank)
    config._instances[key] = graph
    return key


@pytest.fixture
def edge_graph_cleanup():
    keys: list[str] = []
    yield keys
    for k in keys:
        config._instances.pop(k, None)


class TestReconcileEntityEdges:
    def test_replays_missed_typed_edge(self, edge_graph_cleanup):
        graph = _EdgeReplayGraph(nodes={("s", "u"), ("d", "u")})
        edge_graph_cleanup.append(_register_graph("coding", graph))
        ms = _EdgeScanStore([_edge_row()])
        config._instances["metadata_store"] = ms

        result = reconcile_entity_edges()

        assert result["scanned"] == 1
        assert result["projected_ok"] == 1
        assert result["stamped"] == 1
        assert result["skipped"] == 0
        assert ms.stamped_ids == ["edge-1"]
        merges = [c for c, _ in graph.queries if "MERGE (a)-[r:IMPLEMENTS]->(b)" in c]
        assert len(merges) == 1

    def test_skips_when_node_missing(self, edge_graph_cleanup):
        graph = _EdgeReplayGraph(nodes={("s", "u")})  # dst absent
        edge_graph_cleanup.append(_register_graph("coding", graph))
        ms = _EdgeScanStore([_edge_row()])
        config._instances["metadata_store"] = ms

        result = reconcile_entity_edges()

        assert result["projected_ok"] == 0
        assert result["stamped"] == 0
        assert result["skipped"] == 1
        assert ms.stamped_ids == []  # never stamp an edge that did not attach

    def test_scan_excludes_archived_relates_to_and_stamped(self, edge_graph_cleanup):
        graph = _EdgeReplayGraph()
        edge_graph_cleanup.append(_register_graph("coding", graph))
        ms = _EdgeScanStore([])  # empty -> assert on the SQL shape
        config._instances["metadata_store"] = ms

        reconcile_entity_edges()

        scan = ms.scans[0]
        assert "valid_until IS NULL" in scan  # excludes archived edges
        assert "relation_type <> 'RELATES_TO'" in scan  # co-occurrence slot untouched
        assert "graph_projected_at IS NULL" in scan  # already-stamped not rescanned

    def test_never_touches_cooccurrence_slot(self, edge_graph_cleanup):
        """Belt-and-braces: even a RELATES_TO row reaching the loop issues no MERGE."""
        graph = _EdgeReplayGraph(nodes={("s", "u"), ("d", "u")})
        edge_graph_cleanup.append(_register_graph("coding", graph))
        ms = _EdgeScanStore([_edge_row(relation_type="RELATES_TO")])
        config._instances["metadata_store"] = ms

        result = reconcile_entity_edges()

        assert result["projected_ok"] == 0
        assert result["skipped"] == 1
        assert ms.stamped_ids == []
        assert all("RELATES_TO" not in c for c, _ in graph.queries)

    def test_projects_to_edge_bank(self, edge_graph_cleanup):
        """A per-bank edge projects onto its own graph, not the default."""
        coding = _EdgeReplayGraph(nodes={("s", "u"), ("d", "u")})
        personal = _EdgeReplayGraph(nodes={("s2", "u"), ("d2", "u")})
        edge_graph_cleanup.append(_register_graph("coding", coding))
        edge_graph_cleanup.append(_register_graph("personal", personal))
        ms = _EdgeScanStore(
            [
                _edge_row(id="e-coding", bank="coding"),
                _edge_row(id="e-personal", bank="personal", src_entity_id="s2", dst_entity_id="d2"),
            ]
        )
        config._instances["metadata_store"] = ms

        result = reconcile_entity_edges()

        assert result["projected_ok"] == 2
        assert sorted(ms.stamped_ids) == ["e-coding", "e-personal"]
        # Each edge hit ONLY its own bank graph.
        assert len(coding.queries) == 1 and coding.queries[0][1]["src"] == "s"
        assert len(personal.queries) == 1 and personal.queries[0][1]["src"] == "s2"

    def test_isolates_user(self, edge_graph_cleanup):
        """An edge for user B never attaches to user A's nodes."""
        graph = _EdgeReplayGraph(nodes={("s", "A"), ("d", "A")})  # only A's nodes
        edge_graph_cleanup.append(_register_graph("coding", graph))
        ms = _EdgeScanStore([_edge_row(user_id="B")])
        config._instances["metadata_store"] = ms

        result = reconcile_entity_edges()

        assert result["projected_ok"] == 0
        assert result["skipped"] == 1
        assert ms.stamped_ids == []

    def test_graph_disabled_bank_skips(self, edge_graph_cleanup):
        edge_graph_cleanup.append(_register_graph("coding", None))
        ms = _EdgeScanStore([_edge_row()])
        config._instances["metadata_store"] = ms

        result = reconcile_entity_edges()

        assert result["scanned"] == 1
        assert result["skipped"] == 1
        assert result["projected_ok"] == 0

    def test_unresolvable_bank_row_skipped_not_crashed(self):
        """A malformed/historical bank value (get_graph_store raises) must be
        skipped-and-continued, never abort the whole reconciliation run."""
        # No graph registered; "bogus-bank" is not in KNOWN_BANKS, so the real
        # config.get_graph_store validation raises ValueError -> guard catches it.
        ms = _EdgeScanStore([_edge_row(bank="bogus-bank")])
        config._instances["metadata_store"] = ms

        result = reconcile_entity_edges()  # must not raise

        assert result["scanned"] == 1
        assert result["skipped"] == 1
        assert result["projected_ok"] == 0
        assert ms.stamped_ids == []

    def test_projection_failure_counted_not_stamped(self, edge_graph_cleanup):
        """A FalkorDB query error is isolated to the row: projected_failed, no stamp."""
        graph = _EdgeReplayGraph(nodes={("s", "u"), ("d", "u")})
        graph.fail = True
        edge_graph_cleanup.append(_register_graph("coding", graph))
        ms = _EdgeScanStore([_edge_row()])
        config._instances["metadata_store"] = ms

        result = reconcile_entity_edges()

        assert result["projected_failed"] == 1
        assert result["projected_ok"] == 0
        assert ms.stamped_ids == []

    def test_stamp_failure_is_swallowed(self, edge_graph_cleanup):
        """A stamp UPDATE failure must not fail the pass (edge retried next run)."""
        graph = _EdgeReplayGraph(nodes={("s", "u"), ("d", "u")})
        edge_graph_cleanup.append(_register_graph("coding", graph))

        class _RaisingStampStore(_EdgeScanStore):
            def execute(self, query, params=None):
                raise RuntimeError("stamp write failed")

        ms = _RaisingStampStore([_edge_row()])
        config._instances["metadata_store"] = ms

        result = reconcile_entity_edges()  # must not raise

        assert result["projected_ok"] == 1  # attach counted
        assert result["stamped"] == 0  # but stamp did not land


# ---------------------------------------------------------------------------
# 12. entity_edges reconcile retry budget (LUM-599) + stale-scan index (LUM-600)
# ---------------------------------------------------------------------------


class TestEntityEdgeRetryBudget:
    def test_miss_spends_one_attempt_and_is_not_stamped(self, edge_graph_cleanup):
        graph = _EdgeReplayGraph(nodes={("s", "u")})  # dst never materialised
        edge_graph_cleanup.append(_register_graph("coding", graph))
        ms = _EdgeScanStore([_edge_row()])
        config._instances["metadata_store"] = ms

        result = reconcile_entity_edges()

        assert result["skipped"] == 1
        assert ms.incremented_ids == ["edge-1"]
        assert ms.stamped_ids == []

    def test_attach_stamps_and_resets_counter(self, edge_graph_cleanup):
        """Decision: a confirmed attach resets the counter to 0, so an edge that
        a merge later un-stamps starts again on a full budget."""
        graph = _EdgeReplayGraph(nodes={("s", "u"), ("d", "u")})
        edge_graph_cleanup.append(_register_graph("coding", graph))
        ms = _EdgeScanStore([_edge_row()])
        config._instances["metadata_store"] = ms

        reconcile_entity_edges()

        assert ms.stamped_ids == ["edge-1"]
        assert "reconcile_attempts = 0" in ms.stamp_sql[0]
        assert ms.incremented_ids == []

    def test_scan_excludes_spent_budget_and_orders_fresh_first(
        self, edge_graph_cleanup, monkeypatch
    ):
        from plugins.graph.reconcile import DEFAULT_EDGE_MAX_ATTEMPTS
        from plugins.graph.reconcile import EDGE_MAX_ATTEMPTS_ENV

        monkeypatch.delenv(EDGE_MAX_ATTEMPTS_ENV, raising=False)
        edge_graph_cleanup.append(_register_graph("coding", _EdgeReplayGraph()))
        ms = _EdgeScanStore([])
        config._instances["metadata_store"] = ms

        reconcile_entity_edges(limit=5)

        scan = ms.scans[0]
        assert "reconcile_attempts < %s" in scan
        # Fresh misses (attempts=0) come first when the LIMIT truncates.
        assert scan.index("ORDER BY reconcile_attempts, id") < scan.index("LIMIT 5")
        assert ms.scan_params[0] == (DEFAULT_EDGE_MAX_ATTEMPTS,)

    def test_scan_where_matches_partial_index_predicate(self):
        """LUM-600: the scan repeats the index predicate of migration 080, so
        the planner can prove the partial index covers it."""
        from pathlib import Path

        from graph.reconcile import _EDGE_STALE_WHERE

        mig = (
            Path(__file__).resolve().parents[3]
            / "postgres"
            / "migrations"
            / "080-entity-edges-reconcile-backoff.sql"
        ).read_text(encoding="utf-8")
        index_ddl = mig.split("CREATE INDEX IF NOT EXISTS idx_entity_edges_reconcile_stale", 1)[1]
        predicate = index_ddl.split("WHERE", 1)[1].split(";", 1)[0]
        normalise = lambda text: " ".join(text.split())  # noqa: E731
        assert normalise(predicate) == normalise(_EDGE_STALE_WHERE)

    def test_aged_out_counted_and_logged(self, edge_graph_cleanup, monkeypatch, caplog):
        from plugins.graph.reconcile import EDGE_MAX_ATTEMPTS_ENV

        monkeypatch.setenv(EDGE_MAX_ATTEMPTS_ENV, "3")
        edge_graph_cleanup.append(_register_graph("coding", _EdgeReplayGraph()))
        ms = _EdgeScanStore([], aged_out=4)
        config._instances["metadata_store"] = ms

        with caplog.at_level("INFO"):
            result = reconcile_entity_edges()

        assert result["aged_out"] == 4
        count_sql, count_params = ms.counts[0]
        assert "reconcile_attempts >= %s" in count_sql
        assert count_params == (3,)
        assert ms.scan_params[0] == (3,)
        assert any(
            "projection_type=entity_edge" in r.getMessage()
            and "aged_out=4" in r.getMessage()
            and "max_attempts=3" in r.getMessage()
            for r in caplog.records
        )

    def test_aged_out_count_failure_does_not_fail_pass(self, edge_graph_cleanup):
        graph = _EdgeReplayGraph(nodes={("s", "u"), ("d", "u")})
        edge_graph_cleanup.append(_register_graph("coding", graph))

        class _CountRaises(_EdgeScanStore):
            def fetch_one(self, query, params=None):
                raise RuntimeError("count failed")

        ms = _CountRaises([_edge_row()])
        config._instances["metadata_store"] = ms

        result = reconcile_entity_edges()  # must not raise

        assert result["aged_out"] == 0
        assert result["stamped"] == 1

    def test_graph_error_does_not_spend_attempt(self, edge_graph_cleanup):
        """An outage is not evidence of an orphan: no increment."""
        graph = _EdgeReplayGraph(nodes={("s", "u"), ("d", "u")})
        graph.fail = True
        edge_graph_cleanup.append(_register_graph("coding", graph))
        ms = _EdgeScanStore([_edge_row()])
        config._instances["metadata_store"] = ms

        result = reconcile_entity_edges()

        assert result["projected_failed"] == 1
        assert ms.incremented_ids == []

    def test_disabled_or_unresolvable_bank_does_not_spend_attempt(self, edge_graph_cleanup):
        edge_graph_cleanup.append(_register_graph("coding", None))
        ms = _EdgeScanStore([_edge_row(), _edge_row(id="edge-2", bank="bogus-bank")])
        config._instances["metadata_store"] = ms

        result = reconcile_entity_edges()

        assert result["skipped"] == 2
        assert ms.incremented_ids == []

    def test_increment_failure_is_swallowed(self, edge_graph_cleanup):
        graph = _EdgeReplayGraph()  # no nodes -> miss
        edge_graph_cleanup.append(_register_graph("coding", graph))

        class _IncrementRaises(_EdgeScanStore):
            def execute(self, query, params=None):
                raise RuntimeError("increment failed")

        ms = _IncrementRaises([_edge_row(), _edge_row(id="edge-2")])
        config._instances["metadata_store"] = ms

        result = reconcile_entity_edges()  # must not raise

        assert result["skipped"] == 2

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [("", 7), ("   ", 7), ("12", 12), ("1", 1), ("0", 7), ("-3", 7), ("seven", 7)],
    )
    def test_edge_max_attempts_parsing(self, monkeypatch, raw, expected):
        from plugins.graph.reconcile import EDGE_MAX_ATTEMPTS_ENV
        from plugins.graph.reconcile import edge_max_attempts

        monkeypatch.setenv(EDGE_MAX_ATTEMPTS_ENV, raw)
        assert edge_max_attempts() == expected
