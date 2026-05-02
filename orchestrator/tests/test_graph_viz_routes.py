# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Tests for M4 graph visualization API endpoints.

Coverage:
  --- GET /graph/ego ---
  1.  Success: entity found, edges returned
  2.  Entity not found: found=False in response, not a 4xx
  3.  Graph unavailable: available=False in response, not a 5xx
  4.  Truncation: node cap enforced, truncated=true when result hits cap
  5.  min_strength filter applied (threshold reflected in Cypher query)
  6.  user_id always from auth, never from query params

  --- GET /graph/path ---
  7.  Success: path found, nodes and edges built from FalkorDB rows
  8.  No path found: path_found=False, graceful response
  9.  from_entity not found
  10. to_entity not found
  11. Same entity (path_length=0)
  12. Graph unavailable

  --- GET /graph/search ---
  13. Results returned for ≥2 char query
  14. Empty result for query with no matches
  15. Minimum character enforcement (q<2 → empty results, not error)

  --- GET /graph/stats ---
  16. Success: node_count, edge_count, top_entities populated
  17. Graph unavailable: available=False, graceful JSON (no 5xx)
  18. FalkorDB throws inside stats — still returns 200

  --- Auth enforcement ---
  19. 401 when AUTH_ENABLED=true and request unauthenticated (ego)
  20. 401 when AUTH_ENABLED=true and request unauthenticated (stats)

  --- Edge cap ---
  21. Edge cap enforced: truncated=true when edges hit GRAPH_VIZ_MAX_EDGES
"""

import config

import pytest

pytestmark = pytest.mark.skip(
    reason="Core no longer mounts graph viz HTTP routes (owned by services/lumogis-graph).",
)


# ---------------------------------------------------------------------------
# Test-local GraphStore / MetadataStore mocks
# (conftest.py provides global MockMetadataStore and MockVectorStore — we use
#  our own here so tests can inject configurable query results without
#  coupling to the conftest defaults.)
# ---------------------------------------------------------------------------

class MockGraphStore:
    """In-memory GraphStore for viz route tests.

    Results are keyed by a substring that must appear in the Cypher query.
    """

    def __init__(self, query_results=None):
        self._results: dict[str, list[dict]] = query_results or {}
        self.executed: list[str] = []

    def ping(self):
        return True

    def create_node(self, labels, properties):
        return "mock-id"

    def create_edge(self, from_id, to_id, rel_type, properties):
        pass

    def query(self, cypher: str, params: dict | None = None) -> list[dict]:
        self.executed.append(cypher)
        for key, rows in self._results.items():
            if key in cypher:
                return rows
        return []


class MockMetaStore:
    """Configurable MetadataStore for viz tests."""

    def __init__(self, entity_rows=None, search_rows=None, top_rows=None):
        self._entities: dict[str, dict | None] = entity_rows or {}
        self._search: list[dict] = search_rows or []
        self._top: list[dict] = top_rows or []

    def ping(self):
        return True

    def fetch_one(self, sql: str, params=None):
        # Entity name lookup: second param is the entity name
        if params and len(params) >= 2:
            name = params[1]
            return self._entities.get(name)
        return None

    def fetch_all(self, sql: str, params=None):
        if "LIKE" in sql:
            return self._search
        if "mention_count" in sql and "ORDER BY" in sql:
            return self._top
        return []

    def execute(self, sql, params=None):
        pass

    def close(self):
        pass


def _ent(name, eid, etype="PERSON", mention_count=5):
    return {
        "entity_id": eid,
        "name": name,
        "entity_type": etype,
        "aliases": [],
        "mention_count": mention_count,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _client_with(gs, ms):
    """Return a context-manager TestClient with graph + metadata injected.

    Uses `with TestClient(main.app) as c:` which triggers the lifespan and
    therefore load_plugins() — ensuring the graph plugin router is registered.
    The caller is responsible for using this as a context manager.
    """
    config._instances["graph_store"] = gs
    config._instances["metadata_store"] = ms
    import main
    from fastapi.testclient import TestClient
    return TestClient(main.app)


# ---------------------------------------------------------------------------
# 1–6. GET /graph/ego
# ---------------------------------------------------------------------------

class TestEgoEndpoint:

    def test_ego_success(self):
        ada = _ent("Ada Lovelace", "ada-uuid")
        gs = MockGraphStore({"RELATES_TO": [
            {"neighbor_id": "bob-uuid", "neighbor_name": "Bob",
             "neighbor_type": "PERSON", "strength": 5},
        ]})
        ms = MockMetaStore(entity_rows={"Ada Lovelace": ada})

        with _client_with(gs, ms) as c:
            resp = c.get("/graph/ego?entity=Ada+Lovelace")

        assert resp.status_code == 200
        body = resp.json()
        assert body["available"] is True
        assert body["found"] is True
        assert body["entity_id"] == "ada-uuid"
        assert body["entity_name"] == "Ada Lovelace"
        assert body["entity_type"] == "PERSON"
        assert body["node_count"] == 2          # center + 1 neighbor
        assert body["edge_count"] == 1
        assert body["edges"][0]["type"] == "RELATES_TO"
        assert body["edges"][0]["strength"] == 5
        assert body["truncated"] is False

    def test_ego_entity_not_found(self):
        gs = MockGraphStore()
        ms = MockMetaStore()

        with _client_with(gs, ms) as c:
            resp = c.get("/graph/ego?entity=Unknown+Person")

        assert resp.status_code == 200
        body = resp.json()
        assert body["available"] is True
        assert body["found"] is False
        assert body["node_count"] == 0

    def test_ego_graph_unavailable(self):
        config._instances["graph_store"] = None
        import main
        from fastapi.testclient import TestClient

        with TestClient(main.app) as c:
            resp = c.get("/graph/ego?entity=Ada+Lovelace")

        assert resp.status_code == 200
        body = resp.json()
        assert body["available"] is False
        assert "FalkorDB" in body["message"]

    def test_ego_truncation_at_node_cap(self, monkeypatch):
        monkeypatch.setenv("GRAPH_VIZ_MAX_NODES", "3")
        ada = _ent("Ada Lovelace", "ada-uuid")
        neighbors = [
            {"neighbor_id": f"n{i}", "neighbor_name": f"N{i}",
             "neighbor_type": "PERSON", "strength": 5}
            for i in range(10)
        ]
        gs = MockGraphStore({"RELATES_TO": neighbors})
        ms = MockMetaStore(entity_rows={"Ada Lovelace": ada})

        # Re-import the module so _max_nodes() picks up the monkeypatched env var
        import importlib
        import plugins.graph.viz_routes as vr
        importlib.reload(vr)

        with _client_with(gs, ms) as c:
            resp = c.get("/graph/ego?entity=Ada+Lovelace&limit=10")

        assert resp.status_code == 200
        body = resp.json()
        assert body["truncated"] is True
        assert body["node_count"] <= 3

    def test_ego_edge_cap_enforced(self, monkeypatch):
        monkeypatch.setenv("GRAPH_VIZ_MAX_NODES", "200")
        monkeypatch.setenv("GRAPH_VIZ_MAX_EDGES", "2")
        ada = _ent("Ada Lovelace", "ada-uuid")
        neighbors = [
            {"neighbor_id": f"n{i}", "neighbor_name": f"N{i}",
             "neighbor_type": "PERSON", "strength": 5}
            for i in range(10)
        ]
        gs = MockGraphStore({"RELATES_TO": neighbors})
        ms = MockMetaStore(entity_rows={"Ada Lovelace": ada})

        import importlib
        import plugins.graph.viz_routes as vr
        importlib.reload(vr)

        with _client_with(gs, ms) as c:
            resp = c.get("/graph/ego?entity=Ada+Lovelace&limit=10")

        assert resp.status_code == 200
        body = resp.json()
        assert body["edge_count"] <= 2
        assert body["truncated"] is True

    def test_ego_min_strength_applied(self):
        """Cypher executed must reflect the provided min_strength threshold."""
        ada = _ent("Ada Lovelace", "ada-uuid")
        gs = MockGraphStore({"RELATES_TO": []})
        ms = MockMetaStore(entity_rows={"Ada Lovelace": ada})

        with _client_with(gs, ms) as c:
            resp = c.get("/graph/ego?entity=Ada+Lovelace&min_strength=7")

        assert resp.status_code == 200
        # The executed Cypher must contain the threshold value (max(7, schema threshold))
        assert any("RELATES_TO" in q for q in gs.executed)
        # Strength 7 ≥ schema threshold (3), so 7 must appear in the query
        assert any("7" in q for q in gs.executed)

    def test_ego_user_id_never_from_query_params(self):
        """user_id in query string is ignored; route uses auth-derived user_id."""
        ada = _ent("Ada Lovelace", "ada-uuid")
        gs = MockGraphStore({"RELATES_TO": []})
        ms = MockMetaStore(entity_rows={"Ada Lovelace": ada})

        with _client_with(gs, ms) as c:
            resp = c.get("/graph/ego?entity=Ada+Lovelace&user_id=hacker")

        assert resp.status_code == 200
        body = resp.json()
        assert "available" in body   # route processed, no 4xx


# ---------------------------------------------------------------------------
# 7–12. GET /graph/path
# ---------------------------------------------------------------------------

class TestPathEndpoint:

    def test_path_success(self):
        ada = _ent("Ada Lovelace", "ada-uuid")
        bob = _ent("Bob", "bob-uuid")
        path_row = {
            "node_ids": ["ada-uuid", "bob-uuid"],
            "node_names": ["Ada Lovelace", "Bob"],
            "node_types": ["PERSON", "PERSON"],
            "rel_types": ["RELATES_TO"],
            "path_length": 1,
        }
        gs = MockGraphStore({"algo.SPpaths": [path_row]})
        ms = MockMetaStore(entity_rows={"Ada Lovelace": ada, "Bob": bob})

        with _client_with(gs, ms) as c:
            resp = c.get("/graph/path?from_entity=Ada+Lovelace&to_entity=Bob")

        assert resp.status_code == 200
        body = resp.json()
        assert body["available"] is True
        assert body["found"] is True
        assert body["path_found"] is True
        assert body["path_length"] == 1
        assert body["node_count"] == 2
        assert body["edge_count"] == 1

    def test_path_no_path_found(self):
        ada = _ent("Ada Lovelace", "ada-uuid")
        bob = _ent("Bob", "bob-uuid")
        gs = MockGraphStore()   # no algo.SPpaths result
        ms = MockMetaStore(entity_rows={"Ada Lovelace": ada, "Bob": bob})

        with _client_with(gs, ms) as c:
            resp = c.get("/graph/path?from_entity=Ada+Lovelace&to_entity=Bob")

        assert resp.status_code == 200
        body = resp.json()
        assert body["path_found"] is False
        assert body["node_count"] == 0

    def test_path_from_entity_not_found(self):
        bob = _ent("Bob", "bob-uuid")
        gs = MockGraphStore()
        ms = MockMetaStore(entity_rows={"Bob": bob})

        with _client_with(gs, ms) as c:
            resp = c.get("/graph/path?from_entity=Ada+Lovelace&to_entity=Bob")

        assert resp.status_code == 200
        body = resp.json()
        assert body["found"] is False
        assert "Ada Lovelace" in body["message"]

    def test_path_to_entity_not_found(self):
        ada = _ent("Ada Lovelace", "ada-uuid")
        gs = MockGraphStore()
        ms = MockMetaStore(entity_rows={"Ada Lovelace": ada})

        with _client_with(gs, ms) as c:
            resp = c.get("/graph/path?from_entity=Ada+Lovelace&to_entity=Bob")

        assert resp.status_code == 200
        body = resp.json()
        assert body["found"] is False
        assert "Bob" in body["message"]

    def test_path_same_entity(self):
        ada = _ent("Ada Lovelace", "ada-uuid")
        gs = MockGraphStore()
        ms = MockMetaStore(entity_rows={"Ada Lovelace": ada})

        with _client_with(gs, ms) as c:
            resp = c.get("/graph/path?from_entity=Ada+Lovelace&to_entity=Ada+Lovelace")

        assert resp.status_code == 200
        body = resp.json()
        assert body["path_found"] is True
        assert body["path_length"] == 0
        assert body["node_count"] == 1

    def test_path_graph_unavailable(self):
        config._instances["graph_store"] = None
        import main
        from fastapi.testclient import TestClient

        with TestClient(main.app) as c:
            resp = c.get("/graph/path?from_entity=Ada&to_entity=Bob")

        assert resp.status_code == 200
        body = resp.json()
        assert body["available"] is False


# ---------------------------------------------------------------------------
# 13–15. GET /graph/search
# ---------------------------------------------------------------------------

class TestSearchEndpoint:

    def test_search_returns_results(self):
        gs = MockGraphStore()
        ms = MockMetaStore(search_rows=[
            {"entity_id": "ada-uuid", "name": "Ada Lovelace",
             "entity_type": "PERSON", "mention_count": 10},
        ])

        with _client_with(gs, ms) as c:
            resp = c.get("/graph/search?q=Ada")

        assert resp.status_code == 200
        body = resp.json()
        assert len(body["results"]) == 1
        assert body["results"][0]["name"] == "Ada Lovelace"
        assert body["results"][0]["type"] == "PERSON"

    def test_search_empty_results(self):
        gs = MockGraphStore()
        ms = MockMetaStore(search_rows=[])

        with _client_with(gs, ms) as c:
            resp = c.get("/graph/search?q=xyzunknown")

        assert resp.status_code == 200
        assert resp.json()["results"] == []

    def test_search_minimum_characters_enforced(self):
        """q < 2 chars → empty results with a message, not an error."""
        gs = MockGraphStore()
        ms = MockMetaStore()

        with _client_with(gs, ms) as c:
            resp = c.get("/graph/search?q=A")

        assert resp.status_code == 200
        body = resp.json()
        assert body["results"] == []
        assert "2 characters" in body.get("message", "")


# ---------------------------------------------------------------------------
# 16–18. GET /graph/stats
# ---------------------------------------------------------------------------

class TestStatsEndpoint:

    def test_stats_success(self):
        gs = MockGraphStore({
            "count(n)": [{"cnt": 42}],
            "count(r)": [{"cnt": 17}],
        })
        ms = MockMetaStore(top_rows=[
            {"name": "Ada Lovelace", "entity_type": "PERSON", "mention_count": 10},
        ])

        with _client_with(gs, ms) as c:
            resp = c.get("/graph/stats")

        assert resp.status_code == 200
        body = resp.json()
        assert body["available"] is True
        assert body["node_count"] == 42
        assert body["edge_count"] == 17
        assert len(body["top_entities"]) == 1
        assert body["top_entities"][0]["name"] == "Ada Lovelace"

    def test_stats_graph_unavailable(self):
        config._instances["graph_store"] = None
        import main
        from fastapi.testclient import TestClient

        with TestClient(main.app) as c:
            resp = c.get("/graph/stats")

        assert resp.status_code == 200
        body = resp.json()
        assert body["available"] is False
        assert body["node_count"] == 0
        assert "FalkorDB" in body["message"]

    def test_stats_never_5xx_when_graph_throws(self):
        """FalkorDB query errors must not propagate as 5xx."""
        class ThrowingGraphStore:
            def ping(self): return True
            def create_node(self, *a, **k): pass
            def create_edge(self, *a, **k): pass
            def query(self, cypher, params=None):
                raise RuntimeError("simulated failure")

        config._instances["graph_store"] = ThrowingGraphStore()
        ms = MockMetaStore(top_rows=[])
        import main
        from fastapi.testclient import TestClient

        with _client_with(ThrowingGraphStore(), ms) as c:
            resp = c.get("/graph/stats")

        assert resp.status_code == 200
        body = resp.json()
        # available=True because gs is not None; counts degrade to 0
        assert "available" in body


# ---------------------------------------------------------------------------
# 19–20. Auth enforcement
# ---------------------------------------------------------------------------

class TestAuthEnforcement:

    def test_ego_401_when_auth_enabled_and_unauthenticated(self, monkeypatch):
        monkeypatch.setenv("AUTH_ENABLED", "true")
        gs = MockGraphStore()
        ms = MockMetaStore()

        with _client_with(gs, ms) as c:
            resp = c.get("/graph/ego?entity=Ada")

        # auth_middleware intercepts first → 401
        assert resp.status_code == 401

    def test_stats_401_when_auth_enabled_and_unauthenticated(self, monkeypatch):
        monkeypatch.setenv("AUTH_ENABLED", "true")
        gs = MockGraphStore()
        ms = MockMetaStore()

        with _client_with(gs, ms) as c:
            resp = c.get("/graph/stats")

        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Viz page served
# ---------------------------------------------------------------------------

class TestVizPage:

    def test_viz_page_served(self):
        from plugins.graph.viz_routes import _VIZ_HTML
        if not _VIZ_HTML.exists():
            pytest.skip("graph_viz.html not present — skipping page-serve test")

        gs = MockGraphStore()
        ms = MockMetaStore()

        with _client_with(gs, ms) as c:
            resp = c.get("/graph/viz")

        assert resp.status_code == 200
        assert "text/html" in resp.headers.get("content-type", "")
        assert b"cytoscape" in resp.content.lower()
