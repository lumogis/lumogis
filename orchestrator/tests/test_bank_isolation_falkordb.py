# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Live FalkorDB bank isolation tests (LUM-293) — opt-in integration."""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.integration

falkordb = pytest.importorskip("falkordb")


@pytest.fixture
def falkordb_url():
    if not os.environ.get("RUN_FALKORDB_BANK_ISOLATION"):
        pytest.skip(
            "Set RUN_FALKORDB_BANK_ISOLATION=1 for live FalkorDB bank isolation tests"
        )
    url = os.environ.get("FALKORDB_URL")
    if not url:
        pytest.skip("FALKORDB_URL not set")
    return url


def test_falkordb_edge_visible_then_isolated(monkeypatch, falkordb_url):
    monkeypatch.setenv("GRAPH_BACKEND", "falkordb")
    monkeypatch.setenv("FALKORDB_URL", falkordb_url)
    import config

    for key in list(config._instances.keys()):
        if key.startswith("graph_store:"):
            config._instances.pop(key, None)

    coding_gs = config.get_graph_store("coding")
    personal_gs = config.get_graph_store("personal")
    assert coding_gs is not None and personal_gs is not None

    src = "iso-src-293"
    dst = "iso-dst-293"
    uid = "bank-iso-user"

    # LUM-566: seed and query on lumogis_id — the node-key contract the KG writer
    # and entity_edges projection/purge both use. (Was entity_id, a key no writer
    # node carries; keeping it here would fabricate nodes and mask that regression.)
    coding_gs.query(
        "MERGE (a {lumogis_id: $src, user_id: $uid}) "
        "MERGE (b {lumogis_id: $dst, user_id: $uid}) "
        "MERGE (a)-[r:RELATES_TO]->(b) SET r.user_id = $uid",
        {"src": src, "dst": dst, "uid": uid},
    )

    found_coding = coding_gs.query(
        "MATCH (a {lumogis_id: $src})-[r:RELATES_TO]->(b {lumogis_id: $dst}) RETURN count(r) AS c",
        {"src": src, "dst": dst},
    )
    assert found_coding and int(found_coding[0].get("c", found_coding[0].get("_col0", 0))) >= 1

    found_personal = personal_gs.query(
        "MATCH (a {lumogis_id: $src})-[r:RELATES_TO]->(b {lumogis_id: $dst}) RETURN count(r) AS c",
        {"src": src, "dst": dst},
    )
    count = 0
    if found_personal:
        row = found_personal[0]
        count = int(row.get("c", row.get("_col0", 0)))
    assert count == 0
