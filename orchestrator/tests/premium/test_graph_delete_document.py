# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Premium tests for graph delete_document (LUM-160)."""

from __future__ import annotations

from plugins.graph.writer import delete_document
from plugins.graph.writer import project_document
from tests.premium.test_graph_writer import MockGraphStore


def test_delete_document_issues_detach_delete_cypher():
    gs = MockGraphStore()
    project_document(
        gs,
        file_path="/data/docs/report.pdf",
        file_type=".pdf",
        user_id="alice",
    )
    delete_document(gs, file_path="/data/docs/report.pdf", user_id="alice")
    assert gs.queries
    cypher, params = gs.queries[-1]
    assert "DETACH DELETE" in cypher
    assert params["fp"] == "/data/docs/report.pdf"
    assert params["uid"] == "alice"


def test_delete_document_noop_when_graph_none():
    delete_document(None, file_path="/x", user_id="alice")
