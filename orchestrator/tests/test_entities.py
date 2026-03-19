# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Lumogis
"""Tests for services/entities.py and POST /entities/extract."""

import json
import uuid
from dataclasses import dataclass, field
from unittest.mock import MagicMock, call, patch

import config as _config
import pytest
from models.entities import ExtractedEntity
from models.llm import LLMResponse
from services.entities import extract_entities, resolve_entity, store_entities
from tests.conftest import MockEmbedder, MockMetadataStore, MockVectorStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _llm_response(text: str) -> LLMResponse:
    return LLMResponse(text=text, tool_calls=[], stop_reason="stop")


def _entity_json(
    name="Alice",
    entity_type="PERSON",
    aliases=None,
    context_tags=None,
):
    return {
        "name": name,
        "entity_type": entity_type,
        "aliases": aliases or [],
        "context_tags": context_tags or ["engineering"],
    }


class TrackingMetadataStore(MockMetadataStore):
    """MockMetadataStore that records calls and simulates entity lookups."""

    def __init__(self, existing_entity=None):
        super().__init__()
        self.executed: list[tuple] = []
        self._existing = existing_entity  # returned by first fetch_one

    def execute(self, query: str, params: tuple | None = None) -> None:
        self.executed.append((query, params))

    def fetch_one(self, query: str, params: tuple | None = None) -> dict | None:
        return self._existing


# ---------------------------------------------------------------------------
# extract_entities — unit tests (LLM mocked)
# ---------------------------------------------------------------------------


class TestExtractEntities:
    def test_empty_input_returns_empty_list(self):
        assert extract_entities("") == []
        assert extract_entities("   ") == []
        assert extract_entities(None) == []  # type: ignore[arg-type]

    @patch("services.entities.config.get_llm_provider")
    def test_valid_json_parsed_correctly(self, mock_get_llm):
        payload = [_entity_json("Bob", "PERSON", ["Bobby"], ["leadership"])]
        mock_get_llm.return_value.chat.return_value = _llm_response(json.dumps(payload))

        result = extract_entities("Bob led the team.")

        assert len(result) == 1
        assert result[0].name == "Bob"
        assert result[0].entity_type == "PERSON"
        assert result[0].aliases == ["Bobby"]
        assert "leadership" in result[0].context_tags

    @patch("services.entities.config.get_llm_provider")
    def test_multiple_entities_parsed(self, mock_get_llm):
        payload = [
            _entity_json("Alice", "PERSON"),
            _entity_json("Acme Corp", "ORG", [], ["finance"]),
            _entity_json("Project X", "PROJECT"),
        ]
        mock_get_llm.return_value.chat.return_value = _llm_response(json.dumps(payload))

        result = extract_entities("Alice works at Acme Corp on Project X.")

        assert len(result) == 3
        assert {e.name for e in result} == {"Alice", "Acme Corp", "Project X"}

    @patch("services.entities.config.get_llm_provider")
    def test_markdown_code_fence_stripped(self, mock_get_llm):
        payload = [_entity_json()]
        fenced = f"```json\n{json.dumps(payload)}\n```"
        mock_get_llm.return_value.chat.return_value = _llm_response(fenced)

        result = extract_entities("Alice wrote the report.")
        assert len(result) == 1
        assert result[0].name == "Alice"

    @patch("services.entities.config.get_llm_provider")
    def test_non_json_response_returns_empty(self, mock_get_llm):
        mock_get_llm.return_value.chat.return_value = _llm_response("sorry, no entities found")

        result = extract_entities("Some text.")
        assert result == []

    @patch("services.entities.config.get_llm_provider")
    def test_non_list_json_returns_empty(self, mock_get_llm):
        mock_get_llm.return_value.chat.return_value = _llm_response('{"name": "Alice"}')

        result = extract_entities("Alice said hello.")
        assert result == []

    @patch("services.entities.config.get_llm_provider")
    def test_empty_json_array_returns_empty(self, mock_get_llm):
        mock_get_llm.return_value.chat.return_value = _llm_response("[]")

        result = extract_entities("No names here.")
        assert result == []

    @patch("services.entities.config.get_llm_provider")
    def test_malformed_items_skipped_valid_items_kept(self, mock_get_llm):
        payload = [
            {"entity_type": "PERSON"},         # missing 'name'
            _entity_json("Charlie", "CONCEPT"),
        ]
        mock_get_llm.return_value.chat.return_value = _llm_response(json.dumps(payload))

        result = extract_entities("Charlie discussed a concept.")
        assert len(result) == 1
        assert result[0].name == "Charlie"

    @patch("services.entities.config.get_llm_provider")
    def test_llm_exception_returns_empty(self, mock_get_llm):
        mock_get_llm.return_value.chat.side_effect = RuntimeError("LLM unavailable")

        result = extract_entities("Some session text.")
        assert result == []

    @patch("services.entities.config.get_llm_provider")
    def test_original_language_preserved(self, mock_get_llm):
        payload = [_entity_json("Bundesamt für Statistik", "ORG", ["BFS"], ["government"])]
        mock_get_llm.return_value.chat.return_value = _llm_response(json.dumps(payload))

        result = extract_entities("Das Bundesamt für Statistik veröffentlichte neue Daten.")
        assert result[0].name == "Bundesamt für Statistik"


# ---------------------------------------------------------------------------
# store_entities — unit tests
# ---------------------------------------------------------------------------


class TestStoreEntities:
    def _setup(self, existing_entity=None):
        ms = TrackingMetadataStore(existing_entity=existing_entity)
        vs = MockVectorStore()
        emb = MockEmbedder()
        _config._instances["metadata_store"] = ms
        _config._instances["vector_store"] = vs
        _config._instances["embedder"] = emb
        return ms, vs

    def teardown_method(self):
        _config._instances.clear()

    def test_empty_entities_is_noop(self):
        ms, vs = self._setup()
        store_entities([], evidence_id="sess-1", evidence_type="SESSION")
        assert ms.executed == []
        assert vs.count("entities") == 0

    @patch("services.entities.hooks.fire")
    def test_new_entity_inserted(self, mock_fire):
        ms, vs = self._setup(existing_entity=None)
        entity = ExtractedEntity(
            name="Diana",
            entity_type="PERSON",
            aliases=["Di"],
            context_tags=["leadership"],
        )

        store_entities([entity], evidence_id="sess-abc", evidence_type="SESSION", user_id="u1")

        insert_calls = [q for q, _ in ms.executed if "INSERT INTO entities" in q]
        assert len(insert_calls) == 1

        relation_calls = [q for q, _ in ms.executed if "INSERT INTO entity_relations" in q]
        assert len(relation_calls) == 1
        relation_params = next(p for q, p in ms.executed if "INSERT INTO entity_relations" in q)
        assert relation_params[1] == "MENTIONED_IN_SESSION"
        assert relation_params[2] == "SESSION"
        assert relation_params[3] == "sess-abc"

        assert vs.count("entities") == 1
        mock_fire.assert_called_once()
        fired_kwargs = mock_fire.call_args.kwargs
        assert fired_kwargs["name"] == "Diana"
        assert fired_kwargs["evidence_type"] == "SESSION"

    @patch("services.entities.hooks.fire")
    def test_existing_entity_updated_not_inserted(self, mock_fire):
        # Two overlapping context_tags (>= 2) → merge decision
        existing = {
            "entity_id": str(uuid.uuid4()),
            "name": "Diana",
            "aliases": ["Di"],
            "context_tags": ["leadership", "strategy"],
            "mention_count": 2,
        }
        ms, vs = self._setup(existing_entity=existing)
        entity = ExtractedEntity(
            name="Diana",
            entity_type="PERSON",
            aliases=["Diana Prince"],
            context_tags=["strategy", "leadership"],
        )

        store_entities([entity], evidence_id="sess-xyz", evidence_type="SESSION", user_id="u1")

        insert_calls = [q for q, _ in ms.executed if "INSERT INTO entities" in q]
        update_calls = [q for q, _ in ms.executed if "UPDATE entities" in q]
        assert len(insert_calls) == 0
        assert len(update_calls) == 1

        update_params = next(p for q, p in ms.executed if "UPDATE entities" in q)
        merged_aliases = update_params[0]
        merged_tags = update_params[1]
        assert "Di" in merged_aliases
        assert "Diana Prince" in merged_aliases
        assert "leadership" in merged_tags
        assert "strategy" in merged_tags

    @patch("services.entities.hooks.fire")
    def test_relation_type_document(self, mock_fire):
        ms, vs = self._setup(existing_entity=None)
        entity = ExtractedEntity(name="Acme", entity_type="ORG")

        store_entities([entity], evidence_id="/data/report.pdf", evidence_type="DOCUMENT")

        relation_params = next(p for q, p in ms.executed if "INSERT INTO entity_relations" in q)
        assert relation_params[1] == "MENTIONED_IN_DOCUMENT"
        assert relation_params[2] == "DOCUMENT"

    @patch("services.entities.hooks.fire")
    def test_qdrant_upsert_idempotent_same_entity(self, mock_fire):
        ms, vs = self._setup(existing_entity=None)
        entity = ExtractedEntity(name="Eve", entity_type="PERSON", context_tags=["security"])

        store_entities([entity], evidence_id="sess-1", evidence_type="SESSION", user_id="u1")
        point_id_first = vs._collections["entities"][0]["id"]

        # Reset executed to allow a second store call (still no existing entity)
        ms._existing = None
        ms.executed.clear()
        store_entities([entity], evidence_id="sess-2", evidence_type="SESSION", user_id="u1")
        point_id_second = vs._collections["entities"][-1]["id"]

        assert point_id_first == point_id_second, "Same entity should produce same Qdrant point ID"

    @patch("services.entities.hooks.fire")
    def test_multiple_entities_each_fires_hook(self, mock_fire):
        ms, vs = self._setup(existing_entity=None)
        entities = [
            ExtractedEntity(name="Frank", entity_type="PERSON"),
            ExtractedEntity(name="GovOrg", entity_type="ORG"),
        ]

        store_entities(entities, evidence_id="sess-multi", evidence_type="SESSION")

        assert mock_fire.call_count == 2
        fired_names = {c.kwargs["name"] for c in mock_fire.call_args_list}
        assert fired_names == {"Frank", "GovOrg"}

    @patch("services.entities.hooks.fire")
    def test_one_entity_failure_does_not_abort_others(self, mock_fire):
        ms, vs = self._setup(existing_entity=None)
        entities = [
            ExtractedEntity(name="Good Entity", entity_type="CONCEPT"),
            ExtractedEntity(name="Bad Entity", entity_type="PERSON"),
        ]

        original_execute = ms.execute

        def execute_with_bomb(query, params=None):
            if params and "Bad Entity" in str(params):
                raise RuntimeError("DB write failed")
            original_execute(query, params)

        ms.execute = execute_with_bomb

        store_entities(entities, evidence_id="sess-err", evidence_type="SESSION")

        assert mock_fire.call_count == 1
        assert mock_fire.call_args.kwargs["name"] == "Good Entity"

    @patch("services.entities.hooks.fire")
    def test_alias_same_as_name_excluded(self, mock_fire):
        ms, vs = self._setup(existing_entity=None)
        entity = ExtractedEntity(
            name="Alice",
            entity_type="PERSON",
            aliases=["Alice", "Ali"],
        )

        store_entities([entity], evidence_id="sess-1", evidence_type="SESSION")

        insert_params = next(p for q, p in ms.executed if "INSERT INTO entities" in q)
        aliases_stored = insert_params[3]
        assert "Alice" not in aliases_stored
        assert "Ali" in aliases_stored


# ---------------------------------------------------------------------------
# Route: POST /entities/extract
# ---------------------------------------------------------------------------


class TestEntitiesExtractRoute:
    def test_entities_extract_returns_202_accepted(self):
        import main
        from fastapi.testclient import TestClient

        with (
            patch("services.entities.extract_entities", return_value=[]) as mock_extract,
            patch("services.entities.store_entities") as mock_store,
            TestClient(main.app) as client,
        ):
            resp = client.post(
                "/entities/extract",
                json={
                    "text": "Alice met Bob at Acme Corp.",
                    "evidence_id": "sess-route-test",
                    "evidence_type": "SESSION",
                },
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "extraction started"
        assert data["evidence_id"] == "sess-route-test"

    def test_entities_extract_missing_text_returns_422(self):
        import main
        from fastapi.testclient import TestClient

        with TestClient(main.app) as client:
            resp = client.post(
                "/entities/extract",
                json={"evidence_id": "sess-x"},
            )

        assert resp.status_code == 422

    def test_entities_extract_missing_evidence_id_returns_422(self):
        import main
        from fastapi.testclient import TestClient

        with TestClient(main.app) as client:
            resp = client.post(
                "/entities/extract",
                json={"text": "some text"},
            )

        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Route: POST /session/end triggers entity extraction
# ---------------------------------------------------------------------------


class TestSessionEndTriggersExtraction:
    @patch("services.entities.store_entities")
    @patch("services.entities.extract_entities", return_value=[])
    @patch("services.memory.store_session")
    @patch("services.memory.summarize_session")
    def test_session_end_calls_extract_entities(
        self, mock_summarize, mock_store_session, mock_extract, mock_store_entities
    ):
        from models.memory import SessionSummary

        mock_summarize.return_value = SessionSummary(
            session_id="sess-123",
            summary="Test summary",
            topics=["testing"],
            entities=["Alice"],
        )

        import main
        from fastapi.testclient import TestClient

        with TestClient(main.app) as client:
            resp = client.post(
                "/session/end",
                json={
                    "session_id": "sess-123",
                    "messages": [
                        {"role": "user", "content": "Alice wrote the report."},
                        {"role": "assistant", "content": "Got it."},
                    ],
                },
            )

        assert resp.status_code == 200
        mock_extract.assert_called_once()
        call_text = mock_extract.call_args.args[0]
        assert "Alice wrote the report." in call_text
        assert "Got it." in call_text

    @patch("services.entities.store_entities")
    @patch("services.entities.extract_entities", return_value=[])
    @patch("services.memory.store_session")
    @patch("services.memory.summarize_session")
    def test_session_end_calls_store_entities_with_session_id(
        self, mock_summarize, mock_store_session, mock_extract, mock_store_entities
    ):
        from models.memory import SessionSummary

        mock_summarize.return_value = SessionSummary(
            session_id="sess-456",
            summary="Another summary",
        )

        import main
        from fastapi.testclient import TestClient

        with TestClient(main.app) as client:
            client.post(
                "/session/end",
                json={
                    "session_id": "sess-456",
                    "messages": [{"role": "user", "content": "hello"}],
                },
            )

        mock_store_entities.assert_called_once()
        _, kwargs = mock_store_entities.call_args
        assert kwargs["evidence_id"] == "sess-456"
        assert kwargs["evidence_type"] == "SESSION"


# ---------------------------------------------------------------------------
# resolve_entity — unit tests
# ---------------------------------------------------------------------------


class TestResolveEntity:
    def _existing(self, context_tags: list[str]) -> dict:
        return {
            "entity_id": str(uuid.uuid4()),
            "name": "Alice",
            "aliases": [],
            "context_tags": context_tags,
            "mention_count": 1,
        }

    def test_no_existing_returns_new(self):
        entity = ExtractedEntity(name="Alice", entity_type="PERSON", context_tags=["leadership"])
        assert resolve_entity(entity, None) == "new"

    def test_two_tag_overlap_returns_merge(self):
        entity = ExtractedEntity(
            name="Alice", entity_type="PERSON", context_tags=["leadership", "finance"]
        )
        existing = self._existing(["leadership", "finance", "strategy"])
        assert resolve_entity(entity, existing) == "merge"

    def test_exactly_two_tag_overlap_returns_merge(self):
        entity = ExtractedEntity(
            name="Alice", entity_type="PERSON", context_tags=["leadership", "engineering"]
        )
        existing = self._existing(["leadership", "engineering"])
        assert resolve_entity(entity, existing) == "merge"

    def test_one_tag_overlap_returns_ambiguous(self):
        entity = ExtractedEntity(
            name="Alice", entity_type="PERSON", context_tags=["leadership", "finance"]
        )
        existing = self._existing(["leadership", "strategy"])
        assert resolve_entity(entity, existing) == "ambiguous"

    def test_zero_tag_overlap_returns_new(self):
        entity = ExtractedEntity(
            name="Alice", entity_type="PERSON", context_tags=["finance"]
        )
        existing = self._existing(["engineering", "strategy"])
        assert resolve_entity(entity, existing) == "new"

    def test_empty_entity_tags_always_returns_new(self):
        entity = ExtractedEntity(name="Alice", entity_type="PERSON", context_tags=[])
        existing = self._existing(["leadership", "finance"])
        assert resolve_entity(entity, existing) == "new"

    def test_empty_existing_tags_always_returns_new(self):
        entity = ExtractedEntity(
            name="Alice", entity_type="PERSON", context_tags=["leadership"]
        )
        existing = self._existing([])
        assert resolve_entity(entity, existing) == "new"


# ---------------------------------------------------------------------------
# store_entities — ambiguous resolution writes to review_queue
# ---------------------------------------------------------------------------


class TestAmbiguousResolution:
    def _setup(self, existing_entity=None):
        ms = TrackingMetadataStore(existing_entity=existing_entity)
        vs = MockVectorStore()
        emb = MockEmbedder()
        _config._instances["metadata_store"] = ms
        _config._instances["vector_store"] = vs
        _config._instances["embedder"] = emb
        return ms, vs

    def teardown_method(self):
        _config._instances.clear()

    @patch("services.entities.hooks.fire")
    def test_ambiguous_match_logs_to_review_queue(self, mock_fire):
        # 1 overlapping tag → ambiguous
        existing = {
            "entity_id": str(uuid.uuid4()),
            "name": "Alice",
            "aliases": [],
            "context_tags": ["leadership", "finance"],
            "mention_count": 1,
        }
        ms, _ = self._setup(existing_entity=existing)
        entity = ExtractedEntity(
            name="Alice",
            entity_type="PERSON",
            context_tags=["leadership", "engineering"],  # overlap = 1 → ambiguous
        )

        store_entities([entity], evidence_id="sess-amb", evidence_type="SESSION", user_id="u1")

        review_calls = [q for q, _ in ms.executed if "INSERT INTO review_queue" in q]
        assert len(review_calls) == 1

        # A new entity row is also inserted (conservative: keep separate)
        insert_calls = [q for q, _ in ms.executed if "INSERT INTO entities" in q]
        assert len(insert_calls) == 1

    @patch("services.entities.hooks.fire")
    def test_zero_tag_overlap_creates_separate_entity_no_review_queue(self, mock_fire):
        existing = {
            "entity_id": str(uuid.uuid4()),
            "name": "Alice",
            "aliases": [],
            "context_tags": ["engineering"],
            "mention_count": 1,
        }
        ms, _ = self._setup(existing_entity=existing)
        entity = ExtractedEntity(
            name="Alice",
            entity_type="PERSON",
            context_tags=["finance"],  # 0 overlap → new
        )

        store_entities([entity], evidence_id="sess-new", evidence_type="SESSION", user_id="u1")

        review_calls = [q for q, _ in ms.executed if "INSERT INTO review_queue" in q]
        assert len(review_calls) == 0
        insert_calls = [q for q, _ in ms.executed if "INSERT INTO entities" in q]
        assert len(insert_calls) == 1


# ---------------------------------------------------------------------------
# MENTIONED_IN_DOCUMENT — entity extraction triggered from ingest
# ---------------------------------------------------------------------------


class TestIngestEntityExtraction:
    @patch("services.entities.store_entities")
    @patch("services.entities.extract_entities", return_value=[])
    def test_ingest_file_triggers_entity_extraction(self, mock_extract, mock_store):
        """ingest_file() must call extract_entities with the document text."""
        import tempfile
        import os

        # Create a temp .txt file
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, encoding="utf-8"
        ) as f:
            f.write("Alice worked at Acme Corp on the Zurich project.\n")
            tmp_path = f.name

        try:
            from services.ingest import ingest_file

            with (
                patch("services.ingest.config.get_metadata_store") as mock_ms,
                patch("services.ingest.config.get_embedder") as mock_emb,
                patch("services.ingest.config.get_vector_store") as mock_vs,
                patch("services.ingest.config.get_extractors") as mock_ext,
                patch("services.ingest.hooks.fire"),
            ):
                # Configure minimal mocks
                mock_ms.return_value.fetch_one.return_value = None
                mock_ms.return_value.execute.return_value = None
                mock_emb.return_value.embed_batch.return_value = [[0.0] * 768]
                mock_vs.return_value.upsert.return_value = None
                mock_ext.return_value = {".txt": lambda p: open(p).read()}

                ingest_file(tmp_path)

            mock_extract.assert_called_once()
            call_text = mock_extract.call_args.args[0]
            assert "Alice" in call_text
        finally:
            os.unlink(tmp_path)

    @patch("services.entities.store_entities")
    @patch("services.entities.extract_entities")
    def test_ingest_file_stores_with_document_evidence_type(self, mock_extract, mock_store):
        """Entities from ingest must be stored with evidence_type=DOCUMENT."""
        import tempfile
        import os
        from models.entities import ExtractedEntity

        fake_entity = ExtractedEntity(name="Alice", entity_type="PERSON")
        mock_extract.return_value = [fake_entity]

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, encoding="utf-8"
        ) as f:
            f.write("Alice worked at Acme Corp.\n")
            tmp_path = f.name

        try:
            from services.ingest import ingest_file

            with (
                patch("services.ingest.config.get_metadata_store") as mock_ms,
                patch("services.ingest.config.get_embedder") as mock_emb,
                patch("services.ingest.config.get_vector_store") as mock_vs,
                patch("services.ingest.config.get_extractors") as mock_ext,
                patch("services.ingest.hooks.fire"),
            ):
                mock_ms.return_value.fetch_one.return_value = None
                mock_ms.return_value.execute.return_value = None
                mock_emb.return_value.embed_batch.return_value = [[0.0] * 768]
                mock_vs.return_value.upsert.return_value = None
                mock_ext.return_value = {".txt": lambda p: open(p).read()}

                ingest_file(tmp_path)

            mock_store.assert_called_once()
            _, kwargs = mock_store.call_args
            assert kwargs["evidence_type"] == "DOCUMENT"
            assert kwargs["evidence_id"] == tmp_path
        finally:
            os.unlink(tmp_path)


# ---------------------------------------------------------------------------
# Route: GET /entities
# ---------------------------------------------------------------------------


class TestGetEntitiesEndpoint:
    def _setup_ms(self, rows: list[dict]):
        ms = MockMetadataStore()
        ms.fetch_all = lambda q, p=None: rows
        _config._instances["metadata_store"] = ms
        return ms

    def teardown_method(self):
        _config._instances.clear()

    def test_returns_entity_list(self):
        import main
        from fastapi.testclient import TestClient

        rows = [
            {
                "name": "Alice",
                "entity_type": "PERSON",
                "mention_count": 3,
                "aliases": ["Ali"],
                "context_tags": ["leadership"],
            }
        ]
        self._setup_ms(rows)

        with TestClient(main.app) as client:
            resp = client.get("/entities")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["name"] == "Alice"
        assert data[0]["mention_count"] == 3
        assert data[0]["aliases"] == ["Ali"]

    def test_returns_empty_list_when_no_entities(self):
        import main
        from fastapi.testclient import TestClient

        self._setup_ms([])

        with TestClient(main.app) as client:
            resp = client.get("/entities")

        assert resp.status_code == 200
        assert resp.json() == []

    def test_type_filter_param_accepted(self):
        import main
        from fastapi.testclient import TestClient

        self._setup_ms([])

        with TestClient(main.app) as client:
            resp = client.get("/entities?type=Person")

        assert resp.status_code == 200

    def test_pagination_params_accepted(self):
        import main
        from fastapi.testclient import TestClient

        self._setup_ms([])

        with TestClient(main.app) as client:
            resp = client.get("/entities?limit=5&offset=10")

        assert resp.status_code == 200

    def test_invalid_limit_returns_422(self):
        import main
        from fastapi.testclient import TestClient

        with TestClient(main.app) as client:
            resp = client.get("/entities?limit=0")

        assert resp.status_code == 422
