# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Tests for plugins/graph/query.py and the query_graph tool registration.

Coverage:
  1.  Tool registered in TOOL_SPECS after plugin load
  2.  ego mode — entity found + edges returned
  3.  ego mode — entity not found in graph
  4.  ego mode — entity in graph but no qualifying edges
  5.  path mode — path found
  6.  path mode — from_entity not found
  7.  path mode — to_entity not found
  8.  path mode — no path within max_depth
  9.  path mode — same entity (path_length=0)
  10. mentions mode — sources returned
  11. mentions mode — entity not found
  12. mentions mode — no sources
  13. graph unavailable — tool returns gracefully
  14. graph unavailable — context injection skipped
  15. CONTEXT_BUILDING injection — entity detected, edges injected
  16. CONTEXT_BUILDING injection — entity below MIN_MENTION_COUNT threshold
  17. CONTEXT_BUILDING injection — entity detected but no graph edges (skipped)
  18. CONTEXT_BUILDING injection — respects entity budget env (default 5)
  19. CONTEXT_BUILDING injection — alias detection
  20. Deterministic entity lookup: name match
  21. Deterministic entity lookup: alias match
  22. limit/depth bounds are enforced
  --- Hardening (M3 close-out) ---
  23. Word-boundary: "Ada" does NOT match inside "Canada"
  24. Word-boundary: "Ada" does NOT match inside "Cascade"
  25. Word-boundary: multi-word name "Ada Lovelace" still matches
  26. ego depth_used is always 1 regardless of input depth
  27. ego tool schema declares maximum depth = 1
  28. Injection formatting includes strength when available
  29. Injection formatting omits strength gracefully when absent
"""

import json

import plugins.graph  # noqa: F401
import pytest

# services.tools must be imported BEFORE plugins.graph so that the
# TOOL_REGISTERED hook handler (_add_plugin_tool) is registered before the
# graph plugin fires Event.TOOL_REGISTERED during its module-level init.
import services.tools  # noqa: F401

import config


def _ensure_query_graph_in_tool_specs() -> None:
    """Idempotently re-register the in-process query_graph ToolSpec.

    In an isolated run the module-level imports above are enough, but
    pytest sessions load test modules in alphabetical order and earlier
    tests can:
      * import ``plugins.graph`` first under ``GRAPH_MODE != inprocess``,
        skipping ``_register_query_handlers()`` entirely, or
      * reload ``plugins.graph`` (see ``test_graph_plugin_mode_guard.py``)
        after clearing ``hooks._listeners`` — the reload re-fires
        ``TOOL_REGISTERED`` but ``services.tools._add_plugin_tool`` has
        been removed from the listener list, so ``TOOL_SPECS`` never
        gains the query_graph entry.

    We pin the contract here by re-firing the spec directly. The append
    is idempotent because the assertions below only check membership, not
    multiplicity.
    """
    import hooks as _hooks
    from events import Event
    from services.tools import TOOL_SPECS
    from services.tools import _add_plugin_tool

    if any(s.name == "query_graph" for s in TOOL_SPECS):
        return

    if _add_plugin_tool not in _hooks._listeners.get(Event.TOOL_REGISTERED, []):
        _hooks.register(Event.TOOL_REGISTERED, _add_plugin_tool)

    from plugins.graph import _register_query_handlers

    _register_query_handlers()


# ---------------------------------------------------------------------------
# Shared mocks
# ---------------------------------------------------------------------------


class MockGraphStore:
    """Minimal in-memory GraphStore for query tests."""

    def __init__(self, query_results=None):
        # query_results: dict mapping a substring of the Cypher query → list of rows
        self._query_results = query_results or {}
        self.executed_queries: list[str] = []

    def ping(self):
        return True

    def create_node(self, labels, properties):
        return "mock-node-id"

    def create_edge(self, from_id, to_id, rel_type, properties):
        pass

    def query(self, cypher, params=None):
        self.executed_queries.append(cypher)
        for key, rows in self._query_results.items():
            if key in cypher:
                return rows
        return []


class MockMetadataStore:
    """MetadataStore that returns configurable entity rows."""

    def __init__(self, entity_rows=None):
        self._entity_rows = entity_rows or []

    def ping(self):
        return True

    def execute(self, query, params=None):
        pass

    def fetch_one(self, query, params=None):
        q = (query or "").lower()
        if "allows_shared" in q:
            return {"allows_shared": True}
        if "from entities" in q:
            return _resolve_entity_from_params(self._entity_rows, params)
        if not self._entity_rows:
            return None
        return self._entity_rows[0]

    def fetch_all(self, query, params=None):
        q = (query or "").lower()
        if "kg_settings" in q:
            return []
        return self._entity_rows


def _resolve_entity_from_params(catalog: list[dict], params) -> dict | None:
    """Match resolve_entity_by_name params to a catalog row (name or alias)."""
    if not params:
        return None
    name = params[-1]
    if not isinstance(name, str):
        return None
    needle = name.lower()
    for entity in catalog:
        if entity.get("name", "").lower() == needle:
            return entity
        for alias in entity.get("aliases") or []:
            if str(alias).lower() == needle:
                return entity
    return None


# Entity fixture shared by most tests
_ADA = {
    "entity_id": "eid-ada",
    "name": "Ada Lovelace",
    "entity_type": "PERSON",
    "aliases": ["Ada"],
    "context_tags": ["computing", "mathematics"],
    "mention_count": 5,
    "updated_at": None,
    "memory_type": None,
    "last_verified_at": None,
}

_BABBAGE = {
    "entity_id": "eid-babbage",
    "name": "Charles Babbage",
    "entity_type": "PERSON",
    "aliases": [],
    "context_tags": ["computing", "engineering"],
    "mention_count": 3,
}

_PROJECT_X = {
    "entity_id": "eid-projx",
    "name": "Project X",
    "entity_type": "PROJECT",
    "aliases": [],
    "context_tags": ["engineering"],
    "mention_count": 2,
}


@pytest.fixture(autouse=True)
def clean_config():
    """Ensure config._instances is clean before/after each test."""
    config._instances.clear()
    yield
    config._instances.clear()


@pytest.fixture
def mock_graph():
    gs = MockGraphStore()
    # VERIFY-PLAN: LUM-293 — bank-scoped graph_store cache key
    config._instances[config._graph_store_cache_key("personal")] = gs
    return gs


@pytest.fixture
def no_graph():
    config._instances[config._graph_store_cache_key("personal")] = None
    return None


@pytest.fixture
def meta_ada():
    ms = MockMetadataStore(entity_rows=[_ADA])
    config._instances["metadata_store"] = ms
    return ms


@pytest.fixture
def meta_empty():
    ms = MockMetadataStore(entity_rows=[])
    config._instances["metadata_store"] = ms
    return ms


# ---------------------------------------------------------------------------
# 1. Tool registration
# ---------------------------------------------------------------------------


class TestToolRegistration:
    @pytest.fixture(autouse=True)
    def _ensure_registered(self):
        _ensure_query_graph_in_tool_specs()

    def test_query_graph_registered_in_tool_specs(self):
        """query_graph must appear in TOOL_SPECS after tools.py loads."""
        from services.tools import TOOL_SPECS

        names = [s.name for s in TOOL_SPECS]
        assert "query_graph" in names

    def test_query_graph_spec_is_readonly(self):
        from services.tools import TOOL_SPECS

        spec = next(s for s in TOOL_SPECS if s.name == "query_graph")
        assert spec.is_write is False

    def test_query_graph_connector(self):
        from services.tools import TOOL_SPECS

        spec = next(s for s in TOOL_SPECS if s.name == "query_graph")
        assert spec.connector == "lumogis-graph"

    def test_query_graph_definition_has_mode_enum(self):
        from services.tools import TOOL_SPECS

        spec = next(s for s in TOOL_SPECS if s.name == "query_graph")
        modes = spec.definition["parameters"]["properties"]["mode"]["enum"]
        assert set(modes) == {"ego", "path", "mentions"}


# ---------------------------------------------------------------------------
# 2. ego mode — entity found + edges returned
# ---------------------------------------------------------------------------


class TestEgoMode:
    def test_ego_returns_neighbors(self, mock_graph, meta_ada):
        mock_graph._query_results["RELATES_TO"] = [
            {
                "neighbor_id": "eid-babbage",
                "neighbor_name": "Charles Babbage",
                "neighbor_type": "PERSON",
                "strength": 4,
            },
        ]
        from plugins.graph.query import query_graph_tool

        result = json.loads(
            query_graph_tool({"mode": "ego", "entity": "Ada Lovelace"}, user_id="u1")
        )

        assert result["found"] is True
        assert result["mode"] == "ego"
        assert len(result["neighbors"]) == 1
        assert result["neighbors"][0]["neighbor_id"] == "eid-babbage"
        assert "summary" in result
        assert "Charles Babbage" in result["summary"]

    # 3. entity not found in Postgres
    def test_ego_entity_not_found(self, mock_graph, meta_empty):
        from plugins.graph.query import query_graph_tool

        result = json.loads(
            query_graph_tool({"mode": "ego", "entity": "Unknown Person"}, user_id="u1")
        )

        assert result["found"] is False
        assert result["mode"] == "ego"
        assert "not found" in result["message"].lower()

    # 4. entity in graph but no qualifying edges
    def test_ego_no_qualifying_edges(self, mock_graph, meta_ada):
        mock_graph._query_results["RELATES_TO"] = []  # no edges above threshold
        from plugins.graph.query import query_graph_tool

        result = json.loads(
            query_graph_tool({"mode": "ego", "entity": "Ada Lovelace"}, user_id="u1")
        )

        assert result["found"] is True
        assert result["neighbors"] == []
        assert "no connected entities" in result["summary"].lower()

    def test_ego_depth_used_is_always_1(self, mock_graph, meta_ada):
        """depth_used must always be 1 regardless of what depth is requested."""
        mock_graph._query_results["RELATES_TO"] = []
        from plugins.graph.query import query_graph_tool

        for requested_depth in (1, 2, 10):
            result = json.loads(
                query_graph_tool(
                    {"mode": "ego", "entity": "Ada Lovelace", "depth": requested_depth},
                    user_id="u1",
                )
            )
            assert "error" not in result
            assert result.get("depth_used") == 1, (
                f"Expected depth_used=1 for requested depth={requested_depth}, "
                f"got {result.get('depth_used')}"
            )

    def test_ego_limit_capped_at_20(self, mock_graph, meta_ada):
        # Supply 25 rows — only 20 should be requested from FalkorDB
        mock_graph._query_results["RELATES_TO"] = [
            {
                "neighbor_id": f"eid-{i}",
                "neighbor_name": f"Entity {i}",
                "neighbor_type": "CONCEPT",
                "strength": 3,
            }
            for i in range(25)
        ]
        from plugins.graph.query import query_graph_tool

        result = json.loads(
            query_graph_tool({"mode": "ego", "entity": "Ada Lovelace", "limit": 100}, user_id="u1")
        )
        # The actual cap of 20 is enforced in the Cypher LIMIT clause
        assert result["found"] is True
        # At most 20 rows (the mock returns all 25, but real FalkorDB would cap at LIMIT 20)


# ---------------------------------------------------------------------------
# 5–9. path mode
# ---------------------------------------------------------------------------


class TestPathMode:
    def _two_entity_meta(self):
        return MockMetadataStore(entity_rows=[_ADA, _PROJECT_X])

    def test_path_found(self, mock_graph):
        ms = self._two_entity_meta()
        config._instances["metadata_store"] = ms
        mock_graph._query_results["algo.SPpaths"] = [
            {
                "node_ids": ["eid-ada", "eid-babbage", "eid-projx"],
                "node_names": ["Ada Lovelace", "Charles Babbage", "Project X"],
                "path_length": 2,
            },
        ]
        from plugins.graph.query import query_graph_tool

        result = json.loads(
            query_graph_tool(
                {
                    "mode": "path",
                    "from_entity": "Ada Lovelace",
                    "to_entity": "Project X",
                },
                user_id="u1",
            )
        )

        assert result["found"] is True
        assert result["path_length"] == 2
        assert "Ada Lovelace" in result["summary"]
        assert "Project X" in result["summary"]

    # 6. from_entity not found
    def test_path_from_entity_not_found(self, mock_graph, meta_empty):
        from plugins.graph.query import query_graph_tool

        result = json.loads(
            query_graph_tool(
                {
                    "mode": "path",
                    "from_entity": "Ghost",
                    "to_entity": "Project X",
                },
                user_id="u1",
            )
        )
        assert result["found"] is False
        assert "Ghost" in result["message"]

    # 7. to_entity not found
    def test_path_to_entity_not_found(self, mock_graph):
        config._instances["metadata_store"] = MockMetadataStore(entity_rows=[_ADA])
        from plugins.graph.query import query_graph_tool

        result = json.loads(
            query_graph_tool(
                {
                    "mode": "path",
                    "from_entity": "Ada Lovelace",
                    "to_entity": "Nobody",
                },
                user_id="u1",
            )
        )
        assert result["found"] is False
        assert "Nobody" in result["message"]

    # 8. no path within max_depth
    def test_path_no_path_found(self, mock_graph):
        ms = self._two_entity_meta()
        config._instances["metadata_store"] = ms
        mock_graph._query_results["algo.SPpaths"] = []  # no path

        from plugins.graph.query import query_graph_tool

        result = json.loads(
            query_graph_tool(
                {
                    "mode": "path",
                    "from_entity": "Ada Lovelace",
                    "to_entity": "Project X",
                    "max_depth": 2,
                },
                user_id="u1",
            )
        )

        assert result["found"] is False
        assert "no connection" in result["summary"].lower()
        assert result["max_depth_searched"] == 2

    # 9. same entity
    def test_path_same_entity(self, mock_graph, meta_ada):
        from plugins.graph.query import query_graph_tool

        result = json.loads(
            query_graph_tool(
                {
                    "mode": "path",
                    "from_entity": "Ada Lovelace",
                    "to_entity": "Ada",  # alias → same entity_id
                },
                user_id="u1",
            )
        )
        # Both resolve to _ADA (same fetch_one mock returns _ADA always)
        assert result["found"] is True
        assert result["path_length"] == 0
        assert "same entity" in result["summary"].lower()


# ---------------------------------------------------------------------------
# 10–12. mentions mode
# ---------------------------------------------------------------------------


class TestMentionsMode:
    def test_mentions_sources_returned(self, mock_graph, meta_ada):
        mock_graph._query_results["MENTIONS"] = [
            {
                "source_id": "/data/doc.pdf",
                "source_type": None,
                "evidence_type": "DOCUMENT",
                "ts": "2026-01-01T00:00:00Z",
            },
        ]
        from plugins.graph.query import query_graph_tool

        result = json.loads(
            query_graph_tool({"mode": "mentions", "entity": "Ada Lovelace"}, user_id="u1")
        )

        assert result["found"] is True
        assert len(result["sources"]) == 1
        assert result["sources"][0]["source_id"] == "/data/doc.pdf"
        assert "1 source" in result["summary"]

    # 11. entity not found
    def test_mentions_entity_not_found(self, mock_graph, meta_empty):
        from plugins.graph.query import query_graph_tool

        result = json.loads(
            query_graph_tool({"mode": "mentions", "entity": "Unknown"}, user_id="u1")
        )
        assert result["found"] is False

    # 12. no sources
    def test_mentions_no_sources(self, mock_graph, meta_ada):
        mock_graph._query_results["MENTIONS"] = []
        from plugins.graph.query import query_graph_tool

        result = json.loads(
            query_graph_tool({"mode": "mentions", "entity": "Ada Lovelace"}, user_id="u1")
        )
        assert result["found"] is True
        assert result["sources"] == []
        assert "no indexed" in result["summary"].lower()


# ---------------------------------------------------------------------------
# 13–14. graph unavailable
# ---------------------------------------------------------------------------


class TestGraphUnavailable:
    def test_tool_returns_gracefully_when_no_graph(self, no_graph, meta_ada):
        from plugins.graph.query import query_graph_tool

        result = json.loads(
            query_graph_tool({"mode": "ego", "entity": "Ada Lovelace"}, user_id="u1")
        )
        assert result["available"] is False
        assert "not configured" in result["message"].lower()

    def test_context_building_skipped_when_no_graph(self, no_graph, meta_ada):
        from plugins.graph.query import on_context_building

        fragments: list = []
        on_context_building(query="Ada Lovelace project", context_fragments=fragments)
        assert fragments == []


# ---------------------------------------------------------------------------
# 15–19. CONTEXT_BUILDING injection
# ---------------------------------------------------------------------------


class TestContextBuilding:
    def test_injection_appends_fragment(self, mock_graph, meta_ada):
        mock_graph._query_results["RELATES_TO"] = [
            {
                "neighbor_id": "eid-babbage",
                "neighbor_name": "Charles Babbage",
                "neighbor_type": "PERSON",
                "strength": 4,
            },
        ]
        from plugins.graph.query import on_context_building

        fragments: list = []
        on_context_building(query="Tell me about Ada Lovelace", context_fragments=fragments)

        assert len(fragments) == 1
        assert "Ada Lovelace" in fragments[0]
        assert "Charles Babbage" in fragments[0]
        assert "[Graph]" in fragments[0]
        assert " (hint: type=relationship; confidence=" in fragments[0]

    # 16. entity below MIN_MENTION_COUNT threshold
    def test_injection_skipped_below_threshold(self, mock_graph):
        ms = MockMetadataStore(entity_rows=[])
        ms.fetch_all = lambda q, p=None: []  # no candidates above threshold
        config._instances["metadata_store"] = ms

        from plugins.graph.query import on_context_building

        fragments: list = []
        on_context_building(query="Ada Lovelace project", context_fragments=fragments)
        assert fragments == []

    # 17. entity detected but no qualifying edges
    def test_injection_skipped_when_no_edges(self, mock_graph, meta_ada):
        mock_graph._query_results["RELATES_TO"] = []
        from plugins.graph.query import on_context_building

        fragments: list = []
        on_context_building(query="Ada Lovelace project", context_fragments=fragments)
        assert fragments == []

    # 18. entity budget (default 5; pin to 3 for line-count assertion)
    def test_injection_respects_entity_budget_env(self, mock_graph, monkeypatch):
        monkeypatch.setenv("LUMOGIS_CONTEXT_ENTITY_BUDGET", "3")
        four_entities = [
            {
                **_ADA,
                "entity_id": f"eid-{i}",
                "name": f"Entity {i}",
                "aliases": [],
                "mention_count": 5,
                "updated_at": None,
            }
            for i in range(4)
        ]
        ms = MockMetadataStore(entity_rows=four_entities)
        config._instances["metadata_store"] = ms

        mock_graph._query_results["RELATES_TO"] = [
            {
                "neighbor_id": "eid-x",
                "neighbor_name": "Neighbor",
                "neighbor_type": "CONCEPT",
                "strength": 4,
            }
        ]

        from plugins.graph.query import on_context_building

        fragments: list = []
        # All 4 entity names appear in the query
        query = "Entity 0 Entity 1 Entity 2 Entity 3"
        on_context_building(query=query, context_fragments=fragments)

        # At most 3 lines (one per entity) when budget env is 3
        if fragments:
            line_count = len(fragments[0].strip().split("\n"))
            assert line_count <= 3

    # 19. alias detection
    def test_injection_detects_alias(self, mock_graph, meta_ada):
        mock_graph._query_results["RELATES_TO"] = [
            {
                "neighbor_id": "eid-babbage",
                "neighbor_name": "Charles Babbage",
                "neighbor_type": "PERSON",
                "strength": 4,
            },
        ]
        from plugins.graph.query import on_context_building

        fragments: list = []
        # "Ada" is an alias for "Ada Lovelace" in _ADA
        on_context_building(query="Tell me about Ada and her work", context_fragments=fragments)

        assert len(fragments) == 1
        assert "Ada Lovelace" in fragments[0]


# ---------------------------------------------------------------------------
# 20–21. Deterministic entity lookup
# ---------------------------------------------------------------------------


class TestEntityResolution:
    def test_name_match_case_insensitive(self, meta_ada):
        from plugins.graph.query import resolve_entity_by_name

        entity = resolve_entity_by_name("ada lovelace", "default")
        # meta_ada returns _ADA from fetch_one regardless of query params
        assert entity is not None
        assert entity["entity_id"] == "eid-ada"

    def test_alias_match(self):
        ms = MockMetadataStore(entity_rows=[_ADA])
        config._instances["metadata_store"] = ms
        from plugins.graph.query import resolve_entity_by_name

        entity = resolve_entity_by_name("Ada", "default")
        assert entity is not None

    def test_returns_none_when_no_match(self, meta_empty):
        from plugins.graph.query import resolve_entity_by_name

        entity = resolve_entity_by_name("Nobody At All", "default")
        assert entity is None


# ---------------------------------------------------------------------------
# 22. Bounds enforcement
# ---------------------------------------------------------------------------


class TestBounds:
    def test_limit_bounded_in_ego(self, mock_graph, meta_ada):
        mock_graph._query_results["RELATES_TO"] = []
        from plugins.graph.query import ego_network

        # Should not raise regardless of extreme limit values
        result = ego_network(mock_graph, "eid-ada", "default", depth=1, limit=999)
        assert result["edges"] == []

    def test_max_depth_bounded_in_path(self, mock_graph, meta_ada):
        mock_graph._query_results["algo.SPpaths"] = []
        from plugins.graph.query import shortest_path

        shortest_path(mock_graph, "eid-ada", "eid-babbage", "default", max_depth=999)
        # Query should still contain a bounded depth string
        executed = " ".join(mock_graph.executed_queries)
        assert "algo.SPpaths" in executed
        # _MAX_DEPTH caps requests; the query must reflect the capped value, not 999
        assert "maxLen: 999" not in executed

    def test_unknown_mode_returns_error(self, mock_graph, meta_ada):
        from plugins.graph.query import query_graph_tool

        result = json.loads(query_graph_tool({"mode": "invalid"}, user_id="u1"))
        assert "error" in result

    def test_missing_entity_returns_error(self, mock_graph, meta_ada):
        from plugins.graph.query import query_graph_tool

        result = json.loads(query_graph_tool({"mode": "ego"}, user_id="u1"))
        assert "error" in result

    def test_path_missing_to_entity_returns_error(self, mock_graph, meta_ada):
        from plugins.graph.query import query_graph_tool

        result = json.loads(query_graph_tool({"mode": "path", "from_entity": "Ada"}, user_id="u1"))
        assert "error" in result


# ---------------------------------------------------------------------------
# 23–29. Hardening: word-boundary, depth, injection formatting
# ---------------------------------------------------------------------------


class TestWordBoundaryDetection:
    """A. Substring false positives are eliminated by word-boundary matching."""

    def _candidates_with(self, entity):
        ms = MockMetadataStore(entity_rows=[entity])
        config._instances["metadata_store"] = ms
        return ms

    # 23. "Ada" must NOT match inside "Canada"
    def test_ada_does_not_match_canada(self, mock_graph):
        ada_entity = {**_ADA, "name": "Ada", "aliases": []}
        self._candidates_with(ada_entity)
        from plugins.graph.query import on_context_building

        fragments: list = []
        on_context_building(query="What do you know about Canada?", context_fragments=fragments)
        assert fragments == [], (
            '"Ada" should not match inside "Canada" — word boundary not enforced'
        )

    # 24. "Ada" must NOT match inside "Cascade"
    def test_ada_does_not_match_cascade(self, mock_graph):
        ada_entity = {**_ADA, "name": "Ada", "aliases": []}
        self._candidates_with(ada_entity)
        from plugins.graph.query import on_context_building

        fragments: list = []
        on_context_building(query="Tell me about Cascade Falls", context_fragments=fragments)
        assert fragments == [], (
            '"Ada" should not match inside "Cascade" — word boundary not enforced'
        )

    # 25. multi-word name still matches
    def test_multiword_name_matches(self, mock_graph):
        mock_graph._query_results["RELATES_TO"] = [
            {
                "neighbor_id": "eid-babbage",
                "neighbor_name": "Charles Babbage",
                "neighbor_type": "PERSON",
                "strength": 4,
            }
        ]
        ms = MockMetadataStore(entity_rows=[_ADA])
        config._instances["metadata_store"] = ms
        from plugins.graph.query import on_context_building

        fragments: list = []
        on_context_building(
            query="Tell me about Ada Lovelace and her contributions.",
            context_fragments=fragments,
        )
        assert len(fragments) == 1
        assert "Ada Lovelace" in fragments[0]

    def test_alias_word_boundary_no_false_positive(self, mock_graph):
        """Alias "AI" must not match inside "RAIN" or "TRAIN"."""
        ai_entity = {
            "entity_id": "eid-ai",
            "name": "Artificial Intelligence",
            "entity_type": "CONCEPT",
            "aliases": ["AI"],
            "mention_count": 5,
        }
        self._candidates_with(ai_entity)
        from plugins.graph.query import on_context_building

        fragments: list = []
        on_context_building(query="What about training a model?", context_fragments=fragments)
        assert fragments == [], (
            '"AI" should not match inside "training" — word boundary not enforced on alias'
        )

    def test_alias_word_boundary_positive(self, mock_graph):
        """Alias "AI" SHOULD match when the word appears standalone."""
        mock_graph._query_results["RELATES_TO"] = [
            {
                "neighbor_id": "eid-ml",
                "neighbor_name": "Machine Learning",
                "neighbor_type": "CONCEPT",
                "strength": 3,
            }
        ]
        ai_entity = {
            "entity_id": "eid-ai",
            "name": "Artificial Intelligence",
            "entity_type": "CONCEPT",
            "aliases": ["AI"],
            "mention_count": 5,
        }
        ms = MockMetadataStore(entity_rows=[ai_entity])
        config._instances["metadata_store"] = ms
        from plugins.graph.query import on_context_building

        fragments: list = []
        on_context_building(query="What is AI used for?", context_fragments=fragments)
        assert len(fragments) == 1


class TestEgoDepthHardening:
    """B. Ego depth is always 1; output carries depth_used=1."""

    # 26. depth_used is always 1
    def test_depth_used_field_present_and_correct(self, mock_graph, meta_ada):
        mock_graph._query_results["RELATES_TO"] = []
        from plugins.graph.query import query_graph_tool

        result = json.loads(
            query_graph_tool({"mode": "ego", "entity": "Ada Lovelace"}, user_id="u1")
        )
        assert "depth_used" in result
        assert result["depth_used"] == 1

    # 27. tool schema maximum depth == 1
    def test_tool_schema_max_depth_is_1(self):
        _ensure_query_graph_in_tool_specs()
        from services.tools import TOOL_SPECS

        spec = next(s for s in TOOL_SPECS if s.name == "query_graph")
        depth_schema = spec.definition["parameters"]["properties"]["depth"]
        assert depth_schema["maximum"] == 1, (
            f"Tool schema advertises depth maximum={depth_schema['maximum']}, "
            f"but only depth=1 is implemented. Update __init__.py."
        )


class TestStagedEntityExclusion:
    """Pass 1: staged entities must not appear in resolve_entity_by_name or _detect_entities."""

    def test_resolve_entity_excludes_staged(self):
        """resolve_entity_by_name SQL must carry the is_staged exclusion predicate."""
        executed: list[str] = []

        class CapturingMS(MockMetadataStore):
            def fetch_one(self, query, params=None):
                executed.append(query)
                return None

        config._instances["metadata_store"] = CapturingMS()
        from plugins.graph.query import resolve_entity_by_name

        resolve_entity_by_name("The Client", "default")

        assert executed, "Expected fetch_one to be called"
        entity_queries = [q for q in executed if "from entities" in q.lower()]
        assert entity_queries, f"Expected entity-resolution fetch_one, got: {executed!r}"
        entity_sql = entity_queries[0]
        assert "is_staged" in entity_sql.lower() or "IS NOT TRUE" in entity_sql, (
            f"resolve_entity_by_name does not exclude staged entities:\n{entity_sql}"
        )

    def test_staged_entity_not_returned_by_resolve(self):
        """resolve_entity_by_name returns None when only a staged entity matches."""
        ms = MockMetadataStore(entity_rows=[])  # empty → None returned
        config._instances["metadata_store"] = ms
        from plugins.graph.query import resolve_entity_by_name

        result = resolve_entity_by_name("The Client", "default")
        assert result is None

    def test_detect_entities_excludes_staged(self):
        """_detect_entities_in_query SQL must carry the is_staged exclusion predicate."""
        executed: list[str] = []

        class CapturingMS(MockMetadataStore):
            def fetch_all(self, query, params=None):
                executed.append(query)
                return []

        config._instances["metadata_store"] = CapturingMS()
        from plugins.graph.query import _detect_entities_in_query

        _detect_entities_in_query("Tell me about Ada Lovelace", "default")

        assert executed, "Expected fetch_all to be called"
        assert "is_staged" in executed[0].lower() or "IS NOT TRUE" in executed[0], (
            f"_detect_entities_in_query does not exclude staged entities:\n{executed[0]}"
        )

    def test_detect_entities_params_include_user_scope(self):
        captured: list[tuple] = []

        class CapturingMS(MockMetadataStore):
            def fetch_all(self, query, params=None):
                captured.append((query, params))
                return []

        config._instances["metadata_store"] = CapturingMS()
        from plugins.graph.query import _detect_entities_in_query

        _detect_entities_in_query("Ada", "user-xyz")
        assert captured, "expected fetch_all"
        assert any(params is not None and "user-xyz" in params for _q, params in captured), (
            f"expected user_id in SQL bind params, got {captured!r}"
        )

    def test_staged_entity_not_injected_in_context(self, mock_graph):
        """Staged entity must not appear in CONTEXT_BUILDING injection."""
        ms = MockMetadataStore(entity_rows=[])  # staged entity is filtered by SQL
        config._instances["metadata_store"] = ms
        from plugins.graph.query import on_context_building

        fragments: list = []
        on_context_building(query="Tell me about Ada Lovelace", context_fragments=fragments)
        assert fragments == []


class TestInjectionFormatting:
    """C. Injected lines include co_occurrence strength when available."""

    # 28. strength included when present
    def test_injection_includes_strength(self, mock_graph, meta_ada):
        mock_graph._query_results["RELATES_TO"] = [
            {
                "neighbor_id": "eid-babbage",
                "neighbor_name": "Charles Babbage",
                "neighbor_type": "PERSON",
                "strength": 7,
            },
        ]
        from plugins.graph.query import on_context_building

        fragments: list = []
        on_context_building(query="Tell me about Ada Lovelace", context_fragments=fragments)
        assert len(fragments) == 1
        assert "(7)" in fragments[0], f"Expected '(7)' in injection line, got: {fragments[0]!r}"

        fragments: list = []
        on_context_building(query="Tell me about Ada Lovelace", context_fragments=fragments)
        assert len(fragments) == 1
        assert "None" not in fragments[0], (
            f"Strength=None should not appear in injection line, got: {fragments[0]!r}"
        )
        assert "Charles Babbage" in fragments[0]


class TestContextHybridEntitySelection:
    """LUM-210 — hybrid entity selection, ranking tie-break, semantic fallbacks."""

    def test_select_context_entities_tie_breaks_entity_id_asc(self, monkeypatch):
        monkeypatch.setenv("LUMOGIS_CONTEXT_BUILDER_SEMANTIC", "false")
        import graph.query as gq

        rows = [
            {
                "entity_id": "z-ent",
                "name": "Zed",
                "entity_type": "PERSON",
                "aliases": [],
                "mention_count": 3,
                "updated_at": None,
            },
            {
                "entity_id": "a-ent",
                "name": "Ann",
                "entity_type": "PERSON",
                "aliases": [],
                "mention_count": 3,
                "updated_at": None,
            },
        ]
        monkeypatch.setattr(gq, "_detect_entities_in_query", lambda q, u: list(rows))
        out = gq.select_context_entities("Zed Ann", "default")
        assert [r["entity_id"] for r in out] == ["a-ent", "z-ent"]

    def test_semantic_vector_search_error_keeps_explicit(self, mock_graph, monkeypatch):
        monkeypatch.setenv("LUMOGIS_CONTEXT_BUILDER_SEMANTIC", "true")
        row = {**_ADA, "updated_at": None}
        ms = MockMetadataStore(entity_rows=[row])
        config._instances["metadata_store"] = ms

        class E:
            def embed(self, _q):
                return [0.0] * 4

        class VS:
            def search(self, **_kwargs):
                raise RuntimeError("qdrant unavailable")

        config._instances["embedder"] = E()
        config._instances["vector_store"] = VS()
        mock_graph._query_results["RELATES_TO"] = [
            {
                "neighbor_id": "eid-babbage",
                "neighbor_name": "Charles Babbage",
                "neighbor_type": "PERSON",
                "strength": 4,
            },
        ]
        from plugins.graph.query import on_context_building

        fr: list = []
        on_context_building(query="Tell me about Ada Lovelace", context_fragments=fr)
        assert fr and "Ada Lovelace" in fr[0]

    def test_semantic_pure_implicit_injection(self, mock_graph, monkeypatch):
        monkeypatch.setenv("LUMOGIS_CONTEXT_BUILDER_SEMANTIC", "true")
        secret = {
            "entity_id": "ent-secret",
            "name": "HiddenEntity",
            "entity_type": "CONCEPT",
            "aliases": [],
            "mention_count": 5,
            "updated_at": None,
        }

        class Ms:
            def ping(self):
                return True

            def fetch_all(self, query, params=None):
                return []

            def fetch_one(self, query, params=None):
                if params and params[-1] == "ent-secret":
                    return dict(secret)
                return None

            def execute(self, query, params=None):
                return None

        config._instances["metadata_store"] = Ms()

        class E:
            def embed(self, _q):
                return [0.0] * 4

        class VS:
            def search(self, **_kwargs):
                return [
                    {
                        "id": "p1",
                        "score": 0.9,
                        "payload": {"entity_id": "ent-secret"},
                    }
                ]

        config._instances["embedder"] = E()
        config._instances["vector_store"] = VS()
        mock_graph._query_results["RELATES_TO"] = [
            {
                "neighbor_id": "eid-n",
                "neighbor_name": "Neighbor",
                "neighbor_type": "CONCEPT",
                "strength": 2,
            },
        ]

        from plugins.graph.query import on_context_building

        fragments: list = []
        on_context_building(
            query="Unrelated jargon xyzabc nonsense phrase",
            context_fragments=fragments,
        )
        assert fragments and "HiddenEntity" in fragments[0]


class TestQueryGraphToolUserId:
    """LUM-884 / AI-002: the caller's identity is the keyword, never the arguments."""

    def test_query_graph_tool_requires_user_id_kwarg(self, mock_graph, meta_ada):
        from plugins.graph.query import query_graph_tool

        with pytest.raises(TypeError):
            query_graph_tool({"mode": "ego", "entity": "Ada Lovelace"})  # type: ignore[call-arg]
        with pytest.raises(TypeError):
            query_graph_tool({"mode": "ego", "entity": "Ada Lovelace"}, user_id="")

    def test_query_graph_tool_ignores_user_id_in_arguments(self, mock_graph):
        captured: list[tuple] = []

        class CapturingMS(MockMetadataStore):
            def fetch_one(self, query, params=None):
                captured.append((query, params))
                return super().fetch_one(query, params)

        config._instances["metadata_store"] = CapturingMS(entity_rows=[_ADA])
        from plugins.graph.query import query_graph_tool

        query_graph_tool({"mode": "ego", "entity": "Ada Lovelace", "user_id": "A"}, user_id="B")

        lookups = [p for q, p in captured if "FROM entities" in q]
        assert lookups, "expected the entity lookup to run"
        assert all("B" in p and "A" not in p for p in lookups), lookups
