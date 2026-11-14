# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Regression tests for `GET /graph/stats` tenant / visibility binding (LUM-23 / FP-042).

Records Cypher + SQL passed to stores so HTTP 200 alone cannot mask a broken
stats path (`get_stats` swallows graph errors into zero counts).
"""

from __future__ import annotations

import logging
import re
from typing import Any

import pytest
from auth import UserContext
from fastapi import FastAPI
from fastapi.testclient import TestClient
from visibility import visible_cypher_fragment
from visibility import visible_filter

LEGACY_DEFAULT_PRED = re.compile(r"user_id\s*=\s*['\"]default['\"]", re.IGNORECASE)


class RecordingMockGraphStore:
    def __init__(self, *, raise_on_query: bool = False) -> None:
        self.raise_on_query = raise_on_query
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def query(self, cypher: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        self.calls.append((cypher, params))
        if self.raise_on_query:
            raise RuntimeError("simulated graph backend error")
        return [{"cnt": 1}]


class RecordingFakeMetadataStore:
    def __init__(self) -> None:
        self.fetch_calls: list[tuple[str, tuple[Any, ...]]] = []
        self.users: dict[str, dict[str, Any]] = {}

    def fetch_one(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        q = " ".join(sql.split()).lower()
        if "allows_shared from users" in q:
            uid = (params or ())[0]
            row = self.users.get(uid)
            return dict(row) if row else None
        return None

    def fetch_all(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        self.fetch_calls.append((sql, params))
        return [
            {
                "name": "Top",
                "entity_type": "CONCEPT",
                "mention_count": 9,
                "scope": "personal",
                "entity_id": "e1",
            }
        ]


def _stats_client(
    monkeypatch: pytest.MonkeyPatch,
    gs: RecordingMockGraphStore,
    ms: RecordingFakeMetadataStore,
) -> TestClient:
    import graph.viz_routes as viz_mod

    monkeypatch.setattr(viz_mod.config, "get_graph_store", lambda: gs)
    monkeypatch.setattr(viz_mod.config, "get_metadata_store", lambda: ms)
    monkeypatch.setattr(viz_mod.config, "get_cooccurrence_threshold", lambda: 0.37)

    app = FastAPI()
    app.include_router(viz_mod.router)
    return TestClient(app)


def test_stats_cypher_uses_visibility_params_not_literal_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import graph.viz_routes as viz_mod

    monkeypatch.setattr(
        viz_mod,
        "get_user",
        lambda _req: UserContext(user_id="alice"),
    )
    monkeypatch.setenv("AUTH_ENABLED", "false")

    gs = RecordingMockGraphStore()
    client = _stats_client(monkeypatch, gs, RecordingFakeMetadataStore())
    r = client.get("/graph/stats")
    assert r.status_code == 200
    body = r.json()
    assert body["available"] is True
    assert body["cooccurrence_threshold"] == 0.37

    assert len(gs.calls) >= 1
    cypher0, params0 = gs.calls[0]
    assert "$vis_me" in cypher0
    assert params0 == {"vis_me": "alice"}
    assert LEGACY_DEFAULT_PRED.search(cypher0) is None


def test_stats_node_where_matches_visible_cypher_fragment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import graph.viz_routes as viz_mod

    monkeypatch.setattr(
        viz_mod,
        "get_user",
        lambda _req: UserContext(user_id="bob"),
    )
    monkeypatch.setenv("AUTH_ENABLED", "false")

    user = UserContext(user_id="bob")
    expected_vis, expected_params = visible_cypher_fragment(user, alias="n")

    gs = RecordingMockGraphStore()
    client = _stats_client(monkeypatch, gs, RecordingFakeMetadataStore())
    r = client.get("/graph/stats")
    assert r.status_code == 200

    cypher0, params0 = gs.calls[0]
    assert cypher0.startswith("MATCH (n) WHERE ")
    assert cypher0.endswith(" RETURN count(n) AS cnt")
    where_in_query = cypher0.removeprefix("MATCH (n) WHERE ").removesuffix(
        " RETURN count(n) AS cnt"
    )
    assert where_in_query == expected_vis
    assert params0 == expected_params


def test_edge_count_cypher_filters_both_endpoints(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import graph.viz_routes as viz_mod

    monkeypatch.setattr(
        viz_mod,
        "get_user",
        lambda _req: UserContext(user_id="alice"),
    )
    monkeypatch.setenv("AUTH_ENABLED", "false")

    user = UserContext(user_id="alice")
    node_vis, vis_params = visible_cypher_fragment(user, alias="n")
    src_vis, _src_p = visible_cypher_fragment(user, alias="src")
    tgt_vis, _tgt_p = visible_cypher_fragment(user, alias="tgt")

    gs = RecordingMockGraphStore()
    client = _stats_client(monkeypatch, gs, RecordingFakeMetadataStore())
    r = client.get("/graph/stats")
    assert r.status_code == 200

    assert len(gs.calls) >= 2
    _c0, p0 = gs.calls[0]
    cypher1, p1 = gs.calls[1]
    assert p0 == vis_params
    assert p1 == vis_params
    assert src_vis in cypher1
    assert tgt_vis in cypher1
    assert node_vis in gs.calls[0][0]


def test_node_and_edge_queries_bind_vis_params_not_empty_dict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import graph.viz_routes as viz_mod

    monkeypatch.setattr(
        viz_mod,
        "get_user",
        lambda _req: UserContext(user_id="wendy"),
    )
    monkeypatch.setenv("AUTH_ENABLED", "false")

    gs = RecordingMockGraphStore()
    client = _stats_client(monkeypatch, gs, RecordingFakeMetadataStore())
    r = client.get("/graph/stats")
    assert r.status_code == 200

    assert len(gs.calls) >= 2
    for _cy, pr in gs.calls[:2]:
        assert pr == {"vis_me": "wendy"}
        assert pr != {}


def test_stats_cypher_includes_shared_system_union(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import graph.viz_routes as viz_mod

    monkeypatch.setattr(
        viz_mod,
        "get_user",
        lambda _req: UserContext(user_id="alice"),
    )
    monkeypatch.setenv("AUTH_ENABLED", "false")

    gs = RecordingMockGraphStore()
    client = _stats_client(monkeypatch, gs, RecordingFakeMetadataStore())
    assert client.get("/graph/stats").status_code == 200

    cypher0, _ = gs.calls[0]
    assert "OR n.scope IN ['shared','system']" in cypher0


def test_top_entities_sql_uses_visible_filter_params(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import graph.viz_routes as viz_mod

    monkeypatch.setattr(
        viz_mod,
        "get_user",
        lambda _req: UserContext(user_id="carol"),
    )
    monkeypatch.setenv("AUTH_ENABLED", "false")

    user = UserContext(user_id="carol")
    where_sql, where_tuple = visible_filter(user)

    ms = RecordingFakeMetadataStore()
    client = _stats_client(monkeypatch, RecordingMockGraphStore(), ms)
    r = client.get("/graph/stats")
    assert r.status_code == 200

    assert len(ms.fetch_calls) == 1
    sql, params = ms.fetch_calls[0]
    assert where_sql in sql
    assert params == ("carol",)
    assert where_tuple == ("carol",)


def test_top_entities_sql_does_not_contain_unparameterised_user_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import graph.viz_routes as viz_mod

    monkeypatch.setattr(
        viz_mod,
        "get_user",
        lambda _req: UserContext(user_id="carol"),
    )
    monkeypatch.setenv("AUTH_ENABLED", "false")

    ms = RecordingFakeMetadataStore()
    client = _stats_client(monkeypatch, RecordingMockGraphStore(), ms)
    assert client.get("/graph/stats").status_code == 200

    sql, _params = ms.fetch_calls[0]
    assert "'carol'" not in sql
    assert '"carol"' not in sql


def test_different_auth_users_bind_different_vis_me(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import graph.viz_routes as viz_mod

    monkeypatch.setenv("AUTH_ENABLED", "false")

    gs = RecordingMockGraphStore()
    ms = RecordingFakeMetadataStore()
    current = {"user_id": "u1"}
    monkeypatch.setattr(
        viz_mod,
        "get_user",
        lambda _req: UserContext(user_id=current["user_id"]),
    )
    client = _stats_client(monkeypatch, gs, ms)
    assert client.get("/graph/stats").status_code == 200
    assert gs.calls[0][1] == {"vis_me": "u1"}

    gs.calls.clear()
    current["user_id"] = "u2"
    assert client.get("/graph/stats").status_code == 200
    assert gs.calls[0][1] == {"vis_me": "u2"}


def test_stats_requires_auth_when_auth_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AUTH_ENABLED", "true")
    gs = RecordingMockGraphStore()
    client = _stats_client(monkeypatch, gs, RecordingFakeMetadataStore())
    r = client.get("/graph/stats")
    assert r.status_code == 401
    assert len(gs.calls) == 0


def test_stats_swallow_when_query_raises(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    import graph.viz_routes as viz_mod

    monkeypatch.setattr(
        viz_mod,
        "get_user",
        lambda _req: UserContext(user_id="alice"),
    )
    monkeypatch.setenv("AUTH_ENABLED", "false")

    gs = RecordingMockGraphStore(raise_on_query=True)
    client = _stats_client(monkeypatch, gs, RecordingFakeMetadataStore())

    caplog.set_level(logging.WARNING)
    r = client.get("/graph/stats")
    assert r.status_code == 200
    body = r.json()
    assert body["available"] is True
    assert body["node_count"] == 0
    assert body["edge_count"] == 0

    joined = " ".join(rec.getMessage() for rec in caplog.records if rec.levelname == "WARNING")
    assert "graph_viz: stats" in joined
