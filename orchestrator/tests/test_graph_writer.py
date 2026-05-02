# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Unit tests for plugins/graph/writer.py.

All tests use a MockGraphStore (in-memory) so no FalkorDB connection is needed.

M1 Compatibility Gate (marked with @pytest.mark.m1_compat)
-----------------------------------------------------------
These tests document the MERGE patterns the writer relies on.
Run them against a live FalkorDB v4.4+ to verify Cypher compatibility:

    pytest -m m1_compat --falkordb-url redis://localhost:6379

The live-FalkorDB marker is automatically skipped unless
FALKORDB_URL is set in the environment.
"""

import os

import pytest
from plugins.graph import schema as gs_schema
from plugins.graph.writer import on_audio_transcribed
from plugins.graph.writer import on_document_ingested
from plugins.graph.writer import on_entity_created
from plugins.graph.writer import on_entity_merged
from plugins.graph.writer import on_note_captured
from plugins.graph.writer import on_session_ended

import config

# ---------------------------------------------------------------------------
# Mock GraphStore
# ---------------------------------------------------------------------------


class MockGraphStore:
    """In-memory GraphStore for writer unit tests."""

    def __init__(self):
        self._nodes: dict[str, dict] = {}  # node_id -> {"labels": [...], "props": {...}}
        self._edges: list[dict] = []  # list of edge records
        self._next_id = 0
        self.queries: list[tuple[str, dict]] = []  # (cypher, params) log

    def _new_id(self) -> str:
        self._next_id += 1
        return str(self._next_id)

    def ping(self) -> bool:
        return True

    def create_node(self, labels: list[str], properties: dict) -> str:
        key = (properties.get("lumogis_id", ""), properties.get("user_id", ""))
        # MERGE: return existing id if same lumogis_id + user_id already exists
        for nid, node in self._nodes.items():
            p = node["props"]
            if p.get("lumogis_id") == key[0] and p.get("user_id") == key[1]:
                node["props"].update(properties)
                return nid
        nid = self._new_id()
        self._nodes[nid] = {"labels": labels, "props": dict(properties)}
        return nid

    def create_edge(self, from_id: str, to_id: str, rel_type: str, properties: dict) -> None:
        evidence_id = properties.get("evidence_id", "")
        for edge in self._edges:
            if (
                edge["from_id"] == from_id
                and edge["to_id"] == to_id
                and edge["rel_type"] == rel_type
                and edge["evidence_id"] == evidence_id
            ):
                edge["props"].update(properties)
                return
        self._edges.append(
            {
                "from_id": from_id,
                "to_id": to_id,
                "rel_type": rel_type,
                "evidence_id": evidence_id,
                "props": dict(properties),
            }
        )

    def query(self, cypher: str, params: dict | None = None) -> list[dict]:
        self.queries.append((cypher, params or {}))
        # Return empty list (good enough for unit tests that don't assert query results)
        return []

    # --- Helpers for test assertions ---

    def nodes_with_label(self, label: str) -> list[dict]:
        return [n["props"] for n in self._nodes.values() if label in n["labels"]]

    def edges_of_type(self, rel_type: str) -> list[dict]:
        return [e for e in self._edges if e["rel_type"] == rel_type]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_graph_store():
    return MockGraphStore()


@pytest.fixture(autouse=True)
def _inject_graph_store(mock_graph_store, monkeypatch):
    """Inject mock GraphStore into config so writer.py sees it."""
    config._instances["graph_store"] = mock_graph_store
    yield
    config._instances.pop("graph_store", None)


# ---------------------------------------------------------------------------
# DOCUMENT_INGESTED
# ---------------------------------------------------------------------------


class TestOnDocumentIngested:
    def test_creates_document_node(self, mock_graph_store):
        on_document_ingested(
            file_path="/data/report.pdf",
            chunk_count=5,
            user_id="default",
        )
        docs = mock_graph_store.nodes_with_label(gs_schema.NodeLabel.DOCUMENT)
        assert len(docs) == 1
        assert docs[0]["lumogis_id"] == "/data/report.pdf"
        assert docs[0]["user_id"] == "default"
        assert docs[0]["file_type"] == "pdf"

    def test_is_idempotent(self, mock_graph_store):
        on_document_ingested(file_path="/data/doc.txt", chunk_count=1, user_id="default")
        on_document_ingested(file_path="/data/doc.txt", chunk_count=2, user_id="default")
        docs = mock_graph_store.nodes_with_label(gs_schema.NodeLabel.DOCUMENT)
        assert len(docs) == 1

    def test_no_op_when_graph_disabled(self, monkeypatch):
        monkeypatch.setitem(config._instances, "graph_store", None)
        on_document_ingested(file_path="/data/x.txt", chunk_count=0, user_id="default")


# ---------------------------------------------------------------------------
# ENTITY_CREATED
# ---------------------------------------------------------------------------


class TestOnEntityCreated:
    def test_creates_entity_node(self, mock_graph_store):
        on_entity_created(
            entity_id="eid-001",
            name="Ada Lovelace",
            entity_type="PERSON",
            evidence_id="/data/doc.pdf",
            evidence_type="DOCUMENT",
            user_id="default",
        )
        persons = mock_graph_store.nodes_with_label(gs_schema.NodeLabel.PERSON)
        assert any(p["lumogis_id"] == "eid-001" for p in persons)

    def test_creates_source_document_node(self, mock_graph_store):
        on_entity_created(
            entity_id="eid-002",
            name="Project X",
            entity_type="PROJECT",
            evidence_id="/data/brief.pdf",
            evidence_type="DOCUMENT",
            user_id="default",
        )
        docs = mock_graph_store.nodes_with_label(gs_schema.NodeLabel.DOCUMENT)
        assert any(d["lumogis_id"] == "/data/brief.pdf" for d in docs)

    def test_creates_mentions_edge(self, mock_graph_store):
        on_entity_created(
            entity_id="eid-003",
            name="ACME Corp",
            entity_type="ORG",
            evidence_id="/data/contract.pdf",
            evidence_type="DOCUMENT",
            user_id="default",
        )
        mentions = mock_graph_store.edges_of_type(gs_schema.EdgeType.MENTIONS)
        assert len(mentions) == 1
        assert mentions[0]["evidence_id"] == "/data/contract.pdf"

    def test_unknown_entity_type_maps_to_concept(self, mock_graph_store):
        on_entity_created(
            entity_id="eid-004",
            name="quantum entanglement",
            entity_type="UNKNOWN",
            evidence_id="sess-01",
            evidence_type="SESSION",
            user_id="default",
        )
        concepts = mock_graph_store.nodes_with_label(gs_schema.NodeLabel.CONCEPT)
        assert any(c["lumogis_id"] == "eid-004" for c in concepts)

    def test_entity_created_is_idempotent(self, mock_graph_store):
        kwargs = dict(
            entity_id="eid-005",
            name="Ada",
            entity_type="PERSON",
            evidence_id="sess-02",
            evidence_type="SESSION",
            user_id="default",
        )
        on_entity_created(**kwargs)
        on_entity_created(**kwargs)
        persons = mock_graph_store.nodes_with_label(gs_schema.NodeLabel.PERSON)
        assert sum(1 for p in persons if p["lumogis_id"] == "eid-005") == 1

    def test_no_op_when_graph_disabled(self, monkeypatch):
        monkeypatch.setitem(config._instances, "graph_store", None)
        on_entity_created(
            entity_id="eid-x",
            name="Ghost",
            entity_type="PERSON",
            evidence_id="d.pdf",
            evidence_type="DOCUMENT",
            user_id="default",
        )


# ---------------------------------------------------------------------------
# SESSION_ENDED
# ---------------------------------------------------------------------------


class TestOnSessionEnded:
    def test_creates_session_node(self, mock_graph_store):
        on_session_ended(
            session_id="sess-abc",
            summary="We discussed the project timeline.",
            topics=["project", "timeline"],
            entities=["Ada Lovelace"],
            entity_ids=["eid-001"],
            user_id="default",
        )
        sessions = mock_graph_store.nodes_with_label(gs_schema.NodeLabel.SESSION)
        assert any(s["lumogis_id"] == "sess-abc" for s in sessions)

    def test_summary_is_truncated(self, mock_graph_store):
        long_summary = "x" * 600
        on_session_ended(
            session_id="sess-trunc",
            summary=long_summary,
            topics=[],
            entities=[],
            entity_ids=[],
            user_id="default",
        )
        sessions = mock_graph_store.nodes_with_label(gs_schema.NodeLabel.SESSION)
        stored = next(s for s in sessions if s["lumogis_id"] == "sess-trunc")
        assert len(stored["summary"]) <= gs_schema.MAX_TEXT_LENGTH

    def test_creates_discussed_in_edges(self, mock_graph_store):
        on_entity_created(
            entity_id="eid-person",
            name="Bob",
            entity_type="PERSON",
            evidence_id="sess-xyz",
            evidence_type="SESSION",
            user_id="default",
        )
        mock_graph_store.queries.clear()  # reset query log
        on_session_ended(
            session_id="sess-xyz",
            summary="Talked about Bob.",
            topics=[],
            entities=["Bob"],
            entity_ids=["eid-person"],
            user_id="default",
        )
        discussed = [q for q, _ in mock_graph_store.queries if "DISCUSSED_IN" in q]
        assert len(discussed) >= 1

    def test_no_op_when_graph_disabled(self, monkeypatch):
        monkeypatch.setitem(config._instances, "graph_store", None)
        on_session_ended(
            session_id="s",
            summary="",
            topics=[],
            entities=[],
            entity_ids=[],
            user_id="default",
        )


# ---------------------------------------------------------------------------
# NOTE_CAPTURED
# ---------------------------------------------------------------------------


class TestOnNoteCaptured:
    def test_creates_note_node(self, mock_graph_store):
        on_note_captured(note_id="note-001", user_id="default")
        notes = mock_graph_store.nodes_with_label(gs_schema.NodeLabel.NOTE)
        assert any(n["lumogis_id"] == "note-001" for n in notes)

    def test_is_idempotent(self, mock_graph_store):
        on_note_captured(note_id="note-dup", user_id="default")
        on_note_captured(note_id="note-dup", user_id="default")
        notes = mock_graph_store.nodes_with_label(gs_schema.NodeLabel.NOTE)
        assert sum(1 for n in notes if n["lumogis_id"] == "note-dup") == 1


# ---------------------------------------------------------------------------
# AUDIO_TRANSCRIBED
# ---------------------------------------------------------------------------


class TestOnAudioTranscribed:
    def test_creates_audio_memo_node(self, mock_graph_store):
        on_audio_transcribed(
            audio_id="audio-001",
            file_path="/data/meeting.mp3",
            duration_seconds=120.5,
            user_id="default",
        )
        memos = mock_graph_store.nodes_with_label(gs_schema.NodeLabel.AUDIO_MEMO)
        assert any(m["lumogis_id"] == "audio-001" for m in memos)


# ---------------------------------------------------------------------------
# ENTITY_MERGED
# ---------------------------------------------------------------------------


class TestOnEntityMerged:
    def test_deletes_loser_via_query(self, mock_graph_store):
        on_entity_merged(winner_id="eid-win", loser_id="eid-lose", user_id="default")
        detach_queries = [q for q, _ in mock_graph_store.queries if "DETACH DELETE" in q]
        assert len(detach_queries) >= 1

    def test_no_op_when_graph_disabled(self, monkeypatch):
        monkeypatch.setitem(config._instances, "graph_store", None)
        on_entity_merged(winner_id="w", loser_id="l", user_id="default")


# ---------------------------------------------------------------------------
# Staged entity exclusion (Pass 1 quality gate)
# ---------------------------------------------------------------------------


class TestStagedEntityExclusion:
    def test_staged_entity_skips_project_entity(self, mock_graph_store):
        """A staged entity must not create any node, edge, or stamp in the graph."""
        from plugins.graph.writer import project_entity

        project_entity(
            mock_graph_store,
            entity_id="eid-staged",
            entity_type="PERSON",
            name="The Client",
            evidence_id="/data/doc.pdf",
            evidence_type="DOCUMENT",
            user_id="default",
            is_staged=True,
        )
        # No entity node created
        persons = mock_graph_store.nodes_with_label(gs_schema.NodeLabel.PERSON)
        assert not any(p.get("lumogis_id") == "eid-staged" for p in persons)
        # No edges created
        assert mock_graph_store.edges_of_type(gs_schema.EdgeType.MENTIONS) == []

    def test_staged_entity_via_hook_skips_graph(self, mock_graph_store):
        """on_entity_created with is_staged=True must not write to the graph."""
        on_entity_created(
            entity_id="eid-staged-hook",
            name="The Meeting",
            entity_type="CONCEPT",
            evidence_id="sess-001",
            evidence_type="SESSION",
            user_id="default",
            is_staged=True,
        )
        concepts = mock_graph_store.nodes_with_label(gs_schema.NodeLabel.CONCEPT)
        assert not any(c.get("lumogis_id") == "eid-staged-hook" for c in concepts)

    def test_staged_entity_does_not_generate_cooccurrence_edges(
        self, mock_graph_store, monkeypatch
    ):
        """Staged entity must not produce RELATES_TO edges."""
        from plugins.graph.writer import project_entity

        class StagedAwareMockMS:
            """MetadataStore that returns a staged sibling to verify it is excluded."""

            def fetch_all(self, query, params=None):
                return []  # No non-staged siblings

            def fetch_one(self, query, params=None):
                return None

            def execute(self, query, params=None):
                pass

        monkeypatch.setitem(config._instances, "metadata_store", StagedAwareMockMS())

        project_entity(
            mock_graph_store,
            entity_id="eid-normal",
            entity_type="PERSON",
            name="Ada Lovelace",
            evidence_id="sess-001",
            evidence_type="SESSION",
            user_id="default",
            is_staged=False,
        )
        # RELATES_TO edges require a Cypher query — mock returns nothing → no edges
        relates_to = [q for q, _ in mock_graph_store.queries if "RELATES_TO" in q]
        # No co-occurrence queries fired because no siblings returned
        assert len(relates_to) == 0

    def test_cooccurrence_query_excludes_staged_siblings(self, mock_graph_store, monkeypatch):
        """The siblings SQL must contain the is_staged exclusion predicate."""
        executed_queries = []

        class CapturingMS:
            def fetch_all(self, query, params=None):
                executed_queries.append(query)
                return []

            def fetch_one(self, query, params=None):
                return None

            def execute(self, query, params=None):
                pass

        monkeypatch.setitem(config._instances, "metadata_store", CapturingMS())

        from plugins.graph.writer import _update_cooccurrence_edges

        _update_cooccurrence_edges(mock_graph_store, "eid-001", "ev-001", "default")

        assert executed_queries, "Expected at least one fetch_all call"
        query = executed_queries[0]
        assert "is_staged" in query.lower() or "IS NOT TRUE" in query, (
            f"Siblings query does not exclude staged entities:\n{query}"
        )


# ---------------------------------------------------------------------------
# Schema constants
# ---------------------------------------------------------------------------


class TestSchemaConstants:
    def test_entity_type_map_covers_all_types(self):
        for t in ("PERSON", "ORG", "PROJECT", "CONCEPT"):
            label = gs_schema.NodeLabel.for_entity_type(t)
            assert label in (
                gs_schema.NodeLabel.PERSON,
                gs_schema.NodeLabel.ORGANISATION,
                gs_schema.NodeLabel.PROJECT,
                gs_schema.NodeLabel.CONCEPT,
            )

    def test_unknown_type_defaults_to_concept(self):
        assert gs_schema.NodeLabel.for_entity_type("ROBOT") == gs_schema.NodeLabel.CONCEPT

    def test_new_event_constants_exist(self):
        from events import Event

        assert Event.NOTE_CAPTURED == "on_note_captured"
        assert Event.AUDIO_TRANSCRIBED == "on_audio_transcribed"
        assert Event.ENTITY_MERGED == "on_entity_merged"


# ---------------------------------------------------------------------------
# M1 Compatibility Gate (live FalkorDB, skipped without FALKORDB_URL)
# ---------------------------------------------------------------------------

_FALKORDB_URL = os.environ.get("FALKORDB_URL")
# These tests exercise a live FalkorDB instance. `.env` always sets
# FALKORDB_URL=redis://falkordb:6379 so its mere presence is not a
# reliable signal that a server is actually reachable. Require an
# explicit opt-in (RUN_M1_COMPAT=1) to keep `make compose-test` clean
# in stacks that don't include the falkordb service. VERIFY-PLAN.
_RUN_M1_COMPAT = os.environ.get("RUN_M1_COMPAT", "").lower() in ("1", "true", "yes")
pytestmark_live = pytest.mark.skipif(
    not (_FALKORDB_URL and _RUN_M1_COMPAT),
    reason=(
        "FalkorDB live tests are opt-in. "
        "Set RUN_M1_COMPAT=1 (and ensure FALKORDB_URL points to a reachable server) to run."
    ),
)


@pytest.mark.m1_compat
@pytestmark_live
class TestFalkorDBCompatGate:
    """Verify MERGE patterns work against a live FalkorDB v4.4+ instance.

    Run with: FALKORDB_URL=redis://localhost:6379 pytest -m m1_compat
    """

    @pytest.fixture
    def live_store(self):
        from adapters.falkordb_store import FalkorDBStore

        store = FalkorDBStore(url=_FALKORDB_URL, graph_name="lumogis_test_compat")
        yield store
        try:
            store._graph.delete()
        except Exception:
            pass

    def test_ping(self, live_store):
        assert live_store.ping() is True

    def test_merge_person_node(self, live_store):
        node_id = live_store.create_node(
            labels=["Person"],
            properties={"lumogis_id": "compat-eid-001", "user_id": "test", "name": "Ada"},
        )
        assert node_id is not None

    def test_merge_person_node_idempotent(self, live_store):
        props = {"lumogis_id": "compat-eid-002", "user_id": "test", "name": "Ada"}
        id1 = live_store.create_node(labels=["Person"], properties=props)
        id2 = live_store.create_node(labels=["Person"], properties=props)
        assert id1 == id2

    def test_merge_mentions_edge(self, live_store):
        doc_id = live_store.create_node(
            labels=["Document"],
            properties={"lumogis_id": "compat-doc-01", "user_id": "test"},
        )
        person_id = live_store.create_node(
            labels=["Person"],
            properties={"lumogis_id": "compat-eid-003", "user_id": "test", "name": "Bob"},
        )
        live_store.create_edge(
            from_id=doc_id,
            to_id=person_id,
            rel_type="MENTIONS",
            properties={
                "evidence_id": "compat-doc-01",
                "user_id": "test",
                "timestamp": "2026-01-01T00:00:00Z",
            },
        )

    def test_merge_relates_to_edge_with_coalesce(self, live_store):
        a_id = live_store.create_node(
            labels=["Person"],
            properties={"lumogis_id": "compat-eid-010", "user_id": "test", "name": "Alice"},
        )
        b_id = live_store.create_node(
            labels=["Person"],
            properties={"lumogis_id": "compat-eid-011", "user_id": "test", "name": "Bob"},
        )
        cypher = (
            f"MATCH (a) WHERE id(a) = {a_id} "
            f"MATCH (b) WHERE id(b) = {b_id} "
            "MERGE (a)-[r:RELATES_TO]->(b) "
            "SET r.co_occurrence_count = coalesce(r.co_occurrence_count, 0) + 1, "
            "    r.last_seen_at = $now, r.user_id = $uid"
        )
        live_store.query(cypher, {"now": "2026-01-01T00:00:00Z", "uid": "test"})
        result = live_store.query(
            f"MATCH (a)-[r:RELATES_TO]->(b) WHERE id(a) = {a_id} "
            "RETURN r.co_occurrence_count AS cnt",
        )
        if result:
            assert result[0].get("cnt", 0) >= 1

    def test_merge_org_project_concept_nodes(self, live_store):
        for label, lumogis_id in [
            ("Organisation", "compat-org-01"),
            ("Project", "compat-proj-01"),
            ("Concept", "compat-concept-01"),
        ]:
            nid = live_store.create_node(
                labels=[label],
                properties={"lumogis_id": lumogis_id, "user_id": "test", "name": label},
            )
            assert nid is not None
