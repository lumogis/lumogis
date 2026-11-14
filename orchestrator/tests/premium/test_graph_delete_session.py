# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Premium tests for graph delete_session and delete_session_projections (LUM-162, LUM-419)."""

from __future__ import annotations

from plugins.graph.writer import delete_session
from plugins.graph.writer import delete_session_projections
from plugins.graph.writer import project_session
from tests.premium.test_graph_writer import MockGraphStore


def test_delete_session_issues_detach_delete_cypher():
    gs = MockGraphStore()
    project_session(
        gs,
        session_id="sess-del-1",
        summary="summary",
        topics=[],
        entities=[],
        user_id="alice",
    )
    delete_session(gs, session_id="sess-del-1", user_id="alice")
    assert gs.queries
    cypher, params = gs.queries[-1]
    assert "DETACH DELETE" in cypher
    assert params["sid"] == "sess-del-1"
    assert params["uid"] == "alice"


def test_delete_session_noop_when_graph_none():
    delete_session(None, session_id="x", user_id="alice")


# ---------------------------------------------------------------------------
# LUM-419: projection node purge
# ---------------------------------------------------------------------------


def test_project_session_stamps_published_from_on_projection_nodes():
    gs = MockGraphStore()
    project_session(
        gs,
        session_id="proj-sess-1",
        summary="s",
        topics=[],
        entities=[],
        user_id="alice",
        scope="shared",
        published_from="personal-sess-1",
    )
    node = next(iter(gs._nodes.values()))
    assert node["props"]["published_from"] == "personal-sess-1"
    assert node["props"]["scope"] == "shared"  # LUM-961


def test_project_session_omits_published_from_for_personal_nodes():
    gs = MockGraphStore()
    project_session(
        gs,
        session_id="personal-sess-1",
        summary="s",
        topics=[],
        entities=[],
        user_id="alice",
    )
    node = next(iter(gs._nodes.values()))
    assert "published_from" not in node["props"]


def test_delete_session_projections_issues_detach_delete_by_published_from():
    gs = MockGraphStore()
    project_session(
        gs,
        session_id="proj-sess-1",
        summary="s",
        topics=[],
        entities=[],
        user_id="alice",
        scope="shared",
        published_from="personal-sess-src",
    )

    delete_session_projections(gs, source_session_id="personal-sess-src")

    assert gs.queries
    cypher, params = gs.queries[-1]
    assert "DETACH DELETE" in cypher
    assert "published_from" in cypher
    assert params["src_id"] == "personal-sess-src"


def test_delete_session_projections_noop_when_graph_none():
    delete_session_projections(None, source_session_id="x")


def test_delete_session_projections_does_not_reference_entity_labels():
    gs = MockGraphStore()
    from plugins.graph import schema as gs_schema

    gs.create_node(
        labels=[gs_schema.NodeLabel.PERSON],
        properties={"lumogis_id": "e1", "user_id": "alice"},
    )
    project_session(
        gs,
        session_id="proj-sess-shared",
        summary="s",
        topics=[],
        entities=[],
        user_id="alice",
        scope="shared",
        published_from="src-1",
    )

    delete_session_projections(gs, source_session_id="src-1")

    cypher, _ = gs.queries[-1]
    assert gs_schema.NodeLabel.SESSION in cypher
    assert gs_schema.NodeLabel.PERSON not in cypher
