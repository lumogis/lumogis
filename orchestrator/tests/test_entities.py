# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Tests for services/entities.py and POST /entities/extract."""

import inspect
import json
import re
import uuid
from unittest.mock import patch

from models.entities import ExtractedEntity
from models.llm import LLMResponse
from services.entities import extract_entities
from services.entities import resolve_entity
from services.entities import store_entities
from tests.conftest import MockEmbedder
from tests.conftest import MockMetadataStore
from tests.conftest import MockVectorStore

import config as _config

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
            {"entity_type": "PERSON"},  # missing 'name'
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

    @patch("services.entities.hooks.fire_background")
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

    @patch("services.entities.hooks.fire_background")
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

    @patch("services.entities.hooks.fire_background")
    def test_relation_type_document(self, mock_fire):
        ms, vs = self._setup(existing_entity=None)
        entity = ExtractedEntity(name="Acme", entity_type="ORG")

        store_entities([entity], evidence_id="/data/report.pdf", evidence_type="DOCUMENT")

        relation_params = next(p for q, p in ms.executed if "INSERT INTO entity_relations" in q)
        assert relation_params[1] == "MENTIONED_IN_DOCUMENT"
        assert relation_params[2] == "DOCUMENT"

    @patch("services.entities.hooks.fire_background")
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

    @patch("services.entities.hooks.fire_background")
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

    @patch("services.entities.hooks.fire_background")
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

    @patch("services.entities.hooks.fire_background")
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

    # ----------------------------------------------------------------------
    # Migration 012 — entity_relations evidence dedup contract
    # See .cursor/plans/entity_relations_evidence_dedup.plan.md
    # ----------------------------------------------------------------------

    @patch("services.entities.hooks.fire_background")
    def test_store_entities_writer_emits_on_conflict_do_nothing(self, mock_fire):
        """The entity_relations INSERT must carry the dedup ON CONFLICT clause.

        Regression gate for migration 012: if a future edit drops the
        ON CONFLICT target or changes its column list, this test fails
        loudly before the writer can re-introduce duplicate rows.

        Limitation: this regex inspects the *literal* INSERT string passed
        to ms.execute(). Dynamically assembled SQL (e.g. concatenation of
        "ON " + "CONFLICT") would slip past it. That is out of scope for an
        honest-edit regression gate.
        """
        ms, _ = self._setup(existing_entity=None)
        entity = ExtractedEntity(name="OnConflictTest", entity_type="ORG")

        store_entities([entity], evidence_id="sess-oc", evidence_type="SESSION", user_id="u1")

        relation_queries = [q for q, _p in ms.executed if "INSERT INTO entity_relations" in q]
        assert len(relation_queries) == 1, (
            f"expected exactly one entity_relations INSERT, got {len(relation_queries)}"
        )
        query = relation_queries[0]
        # Tolerate whitespace variation; lock the column tuple + DO NOTHING action.
        pattern = re.compile(
            r"ON\s+CONFLICT\s*\(\s*source_id\s*,\s*evidence_id\s*,\s*"
            r"relation_type\s*,\s*user_id\s*\)\s*DO\s+NOTHING",
            re.IGNORECASE,
        )
        assert pattern.search(query), (
            "entity_relations INSERT is missing the post-012 dedup clause "
            "ON CONFLICT (source_id, evidence_id, relation_type, user_id) DO NOTHING. "
            f"Got query: {query!r}"
        )

    @patch("services.entities.hooks.fire_background")
    def test_store_entities_is_idempotent_on_repeat_evidence(self, mock_fire):
        """Writer-shape idempotency contract (NOT row-collapse — see below).

        Mocks cannot exercise the real Postgres ON CONFLICT path, so this
        test does not verify that the database collapses duplicate rows
        (that is owned by the unique index from migration 012). What it
        DOES verify is the writer-side contract that downstream subscribers
        depend on: every store_entities call for a given (entity, evidence)
        pair fires exactly one ENTITY_CREATED hook and emits exactly one
        entity_relations INSERT carrying the ON CONFLICT clause — even on
        repeat ingest. Together with Test 1 (clause shape) and the unique
        index itself (migration 012), this nails down the end-to-end dedup
        contract from this layer's perspective.
        """
        ms, _ = self._setup(existing_entity=None)
        entity = ExtractedEntity(name="RepeatEntity", entity_type="PERSON")

        store_entities([entity], evidence_id="sess-rep", evidence_type="SESSION", user_id="u1")
        ms._existing = None  # second call: writer also sees no prior row
        store_entities([entity], evidence_id="sess-rep", evidence_type="SESSION", user_id="u1")

        relation_queries = [q for q, _p in ms.executed if "INSERT INTO entity_relations" in q]
        assert len(relation_queries) == 2, (
            f"expected one entity_relations INSERT per call, got {len(relation_queries)}"
        )
        for query in relation_queries:
            assert "ON CONFLICT" in query.upper(), (
                "every entity_relations INSERT must carry the dedup clause"
            )

        assert mock_fire.call_count == 2, (
            "ENTITY_CREATED must fire on every call — downstream subscribers "
            "(graph projection, audit) depend on this signal even when the "
            "underlying row is dropped by ON CONFLICT DO NOTHING"
        )

    def test_tools_query_entity_uses_distinct_on_for_recent_relations(self):
        """services/tools.py:_query_entity must use DISTINCT ON wrapped in a
        subquery with an outer ORDER BY created_at DESC LIMIT 10.

        The subquery wrapper preserves "10 most recent distinct mentions"
        semantics. A naked DISTINCT ON ... ORDER BY evidence_id, ... LIMIT 10
        would return the 10 alphabetically-first evidence_ids instead — a
        silent correctness regression. This test fails on that shape.
        """
        from services import tools as tools_module

        source = inspect.getsource(tools_module._query_entity)
        # Collapse Python string-concatenation whitespace and quote chars so
        # the SQL pattern matches across split string literals (e.g.
        # `") sub " "ORDER BY..."`).
        flat = re.sub(r"['\"\s]+", " ", source)

        # Lock the canonical shape: DISTINCT ON inside a subquery, with
        # outer ORDER BY created_at DESC LIMIT 10 outside the closing ")".
        inner_pattern = re.compile(
            r"DISTINCT\s+ON\s*\(\s*evidence_id\s*,\s*relation_type\s*\)",
            re.IGNORECASE,
        )
        assert inner_pattern.search(flat), (
            "services/tools.py:_query_entity must use DISTINCT ON "
            "(evidence_id, relation_type) on the entity_relations SELECT."
        )

        outer_pattern = re.compile(
            r"\)\s*sub\s+ORDER\s+BY\s+created_at\s+DESC\s+LIMIT\s+10",
            re.IGNORECASE,
        )
        assert outer_pattern.search(flat), (
            "services/tools.py:_query_entity DISTINCT ON SELECT must be "
            "wrapped in a subquery aliased `sub`, with the outer query "
            "applying ORDER BY created_at DESC LIMIT 10. A naked DISTINCT "
            "ON ... LIMIT 10 returns the 10 alphabetically-first "
            "evidence_ids instead of the 10 most recent — a silent "
            "correctness regression."
        )

    def test_tools_query_entity_qdrant_fallback_uses_visible_filter(self):
        """services/tools.py:_query_entity Qdrant fallback MUST go through
        :func:`visibility.visible_qdrant_filter`, mirroring the Postgres
        path's :func:`visibility.visible_filter` use.

        Asymmetry here is a real household-sharing leak: Alice publishes
        an entity as ``shared``; Bob's exact-name lookup hits the Postgres
        path and finds it (visible_filter admits shared/system); but if
        the Qdrant fallback uses a raw ``user_id`` payload filter, Bob's
        near-miss semantic lookup silently drops the row. Pin both halves
        of the contract by source inspection so a regression cannot
        re-introduce a bare ``{"key": "user_id", "match": ...}`` shape on
        this code path.
        """
        from services import tools as tools_module

        source = inspect.getsource(tools_module._query_entity)

        # Both helpers must be referenced — Postgres path AND Qdrant path
        # resolve through the household visibility rule.
        assert "visible_filter" in source, (
            "services/tools.py:_query_entity Postgres lookup must use "
            "visibility.visible_filter so shared/system entities are "
            "reachable by exact name."
        )
        assert "visible_qdrant_filter" in source, (
            "services/tools.py:_query_entity Qdrant fallback must use "
            "visibility.visible_qdrant_filter so shared/system entities "
            "are reachable by semantic similarity. A raw "
            '`{"key": "user_id", ...}` payload filter is a sharing leak.'
        )

        # Defence-in-depth: forbid a bare user_id-only payload-filter
        # shape on the Qdrant call site (collapse whitespace + quotes
        # like the existing DISTINCT ON test). This catches the legacy
        # `filter={"must": [{"key": "user_id", "match": {"value": ...}}]}`
        # being re-introduced under the `else:` branch.
        flat = re.sub(r"\s+", "", source)
        legacy_shape = (
            'filter={"must":[{"key":"user_id","match":{"value":user_id}}]}'
        )
        assert legacy_shape not in flat, (
            "services/tools.py:_query_entity Qdrant fallback re-introduced "
            "the legacy user_id-only payload filter — this breaks the "
            "Postgres↔Qdrant visibility symmetry. Use visible_qdrant_filter."
        )


def test_restore_path_remains_generic_on_conflict_do_nothing():
    """routes/admin.py restore must use generic ON CONFLICT DO NOTHING (no
    inference target column list).

    This is a forward-compatibility contract: a generic clause is satisfied
    by ANY unique constraint on the target table, including the new
    migration-012 unique index on entity_relations. If a future edit
    inserts an explicit (col1, col2, ...) target list here, the restore
    would break for any table whose unique constraint differs from that
    list. This test pins the generic shape.
    """
    from routes import admin as admin_module

    source = inspect.getsource(admin_module)

    # Pattern: ON CONFLICT immediately followed by DO NOTHING (no parens).
    pattern = re.compile(r"ON\s+CONFLICT\s+DO\s+NOTHING", re.IGNORECASE)
    matches = pattern.findall(source)
    assert matches, (
        "routes/admin.py restore must emit a generic 'ON CONFLICT DO NOTHING' "
        "(no inference target) so it stays forward-compatible with any unique "
        "constraint on the target table — including the post-012 unique index "
        "on entity_relations(source_id, evidence_id, relation_type, user_id)."
    )

    # Also assert that no ON CONFLICT in admin.py carries an explicit target
    # column list — a stricter pin to catch partial reverts.
    explicit_target = re.compile(r"ON\s+CONFLICT\s*\([^)]+\)\s*DO\s+NOTHING", re.IGNORECASE)
    assert not explicit_target.search(source), (
        "routes/admin.py restore must NOT use ON CONFLICT (target_cols) DO NOTHING — "
        "the generic form is required for cross-table forward-compatibility."
    )


# ---------------------------------------------------------------------------
# Route: POST /entities/extract
# ---------------------------------------------------------------------------


class TestEntitiesExtractRoute:
    def test_entities_extract_returns_queued(self):
        import main
        from fastapi.testclient import TestClient

        with patch("routes.data.enqueue", return_value=1) as mock_enq, TestClient(main.app) as client:
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
        assert data["status"] == "extraction queued"
        assert data["evidence_id"] == "sess-route-test"
        mock_enq.assert_called_once()
        assert mock_enq.call_args.kwargs["kind"] == "entities_extract"

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
    @patch("routes.data.enqueue", return_value=1)
    def test_session_end_enqueues_with_messages_in_payload(self, mock_enqueue):
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
        assert resp.json()["status"] == "session end queued"
        mock_enqueue.assert_called_once()
        payload = mock_enqueue.call_args.kwargs["payload"]
        assert payload["session_id"] == "sess-123"
        msgs = payload["messages"]
        assert any(m["content"] == "Alice wrote the report." for m in msgs)
        assert any(m["content"] == "Got it." for m in msgs)

    @patch("routes.data.enqueue", return_value=1)
    def test_session_end_enqueues_session_id_for_downstream_evidence(
        self, mock_enqueue
    ):
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

        payload = mock_enqueue.call_args.kwargs["payload"]
        assert payload["session_id"] == "sess-456"
        assert mock_enqueue.call_args.kwargs["kind"] == "session_end"


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
        entity = ExtractedEntity(name="Alice", entity_type="PERSON", context_tags=["finance"])
        existing = self._existing(["engineering", "strategy"])
        assert resolve_entity(entity, existing) == "new"

    def test_empty_entity_tags_always_returns_new(self):
        entity = ExtractedEntity(name="Alice", entity_type="PERSON", context_tags=[])
        existing = self._existing(["leadership", "finance"])
        assert resolve_entity(entity, existing) == "new"

    def test_empty_existing_tags_always_returns_new(self):
        entity = ExtractedEntity(name="Alice", entity_type="PERSON", context_tags=["leadership"])
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

    @patch("services.entities.hooks.fire_background")
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

    @patch("services.entities.hooks.fire_background")
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
        import os
        import tempfile

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

                ingest_file(tmp_path, user_id="test-user")

            mock_extract.assert_called_once()
            call_text = mock_extract.call_args.args[0]
            assert "Alice" in call_text
        finally:
            os.unlink(tmp_path)

    @patch("services.entities.store_entities")
    @patch("services.entities.extract_entities")
    def test_ingest_file_stores_with_document_evidence_type(self, mock_extract, mock_store):
        """Entities from ingest must be stored with evidence_type=DOCUMENT."""
        import os
        import tempfile

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

                ingest_file(tmp_path, user_id="test-user")

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
        def _fetch_all(q, p=None):
            # Return entity rows only for entity queries.  Other queries fired
            # during lifespan startup (e.g. feed_monitor loading FROM sources)
            # must receive an empty list to avoid KeyError on missing columns.
            if "FROM entities" in q:
                return rows
            return []
        ms.fetch_all = _fetch_all
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
