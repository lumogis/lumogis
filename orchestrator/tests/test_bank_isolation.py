# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Bank isolation unit tests (LUM-293)."""

from __future__ import annotations

from datetime import datetime
from datetime import timezone
from unittest.mock import Mock

import pytest
from models.mcp_write import AddMemoryInput

import config
from services import banks
from services import entity_edges
from services import recall as R

AS_OF = datetime(2026, 6, 25, tzinfo=timezone.utc)


class FakeMS:
    def __init__(self, results):
        self._results = list(results)
        self.calls = []

    def fetch_all(self, query, params=None):
        self.calls.append((query, params))
        return self._results.pop(0) if self._results else []

    def fetch_one(self, query, params=None):
        if "allows_shared" in (query or "").lower():
            return {"allows_shared": True}
        return None


class FakeEmbedder:
    def embed(self, text):
        return [0.1, 0.2, 0.3]


def test_qdrant_bank_filter_coding():
    clause = banks.qdrant_bank_filter("coding")
    assert clause == [{"key": "bank", "match": {"value": "coding"}}]


def test_qdrant_bank_filter_wildcard():
    assert banks.qdrant_bank_filter("*") is None


def test_write_rejects_wildcard_bank():
    with pytest.raises(ValueError):
        AddMemoryInput(content="x", bank="*")


def test_recall_invalid_bank(caplog):
    import logging

    with caplog.at_level(logging.WARNING, logger="services.recall"):
        out = R.recall(
            user_id="u",
            bank="BAD BANK",
            query="q",
            ms=FakeMS([]),
            embedder=FakeEmbedder(),
            vs=Mock(),
        )
    assert out == []
    assert any("invalid bank" in rec.message for rec in caplog.records)


def test_recall_semantic_bank_filter():
    from tests._fakes import MockVectorStore

    vs = MockVectorStore()
    vs.create_collection("memories", 3)
    vs.upsert(
        "memories",
        "p-c",
        [0.1, 0.2, 0.3],
        {"memory_id": "mc", "user_id": "u", "bank": "coding"},
    )
    vs.upsert(
        "memories",
        "p-p",
        [0.1, 0.2, 0.3],
        {"memory_id": "mp", "user_id": "u", "bank": "personal"},
    )
    out = R._leg_semantic("q", user_id="u", bank="coding", embedder=FakeEmbedder(), vs=vs)
    assert out == ["mc"]


def test_recall_semantic_cross_bank():
    from tests._fakes import MockVectorStore

    vs = MockVectorStore()
    vs.create_collection("memories", 3)
    vs.upsert(
        "memories",
        "p-c",
        [0.1, 0.2, 0.3],
        {"memory_id": "mc", "user_id": "u", "bank": "coding"},
    )
    vs.upsert(
        "memories",
        "p-p",
        [0.1, 0.2, 0.3],
        {"memory_id": "mp", "user_id": "u", "bank": "personal"},
    )
    out = R._leg_semantic("q", user_id="u", bank="*", embedder=FakeEmbedder(), vs=vs)
    assert set(out) == {"mc", "mp"}


def test_recall_fused_cross_bank():
    from tests._fakes import MockVectorStore

    vs = MockVectorStore()
    vs.create_collection("memories", 3)
    vs.upsert(
        "memories",
        "p-c",
        [0.1, 0.2, 0.3],
        {"memory_id": "mc", "user_id": "u", "bank": "coding"},
    )
    vs.upsert(
        "memories",
        "p-p",
        [0.1, 0.2, 0.3],
        {"memory_id": "mp", "user_id": "u", "bank": "personal"},
    )
    ms = FakeMS(
        [
            [
                {
                    "id": "mc",
                    "content": "c",
                    "bank": "coding",
                    "valid_from": AS_OF,
                    "valid_until": None,
                },
                {
                    "id": "mp",
                    "content": "p",
                    "bank": "personal",
                    "valid_from": AS_OF,
                    "valid_until": None,
                },
            ],
            [{"evidence_id": "mc", "entity_ids": []}, {"evidence_id": "mp", "entity_ids": []}],
        ]
    )
    out = R.recall(
        user_id="u",
        bank="*",
        query="q",
        retrieval_strategies=["semantic"],
        as_of=AS_OF,
        rerank=False,
        ms=ms,
        embedder=FakeEmbedder(),
        vs=vs,
    )
    assert {m.id for m in out} == {"mc", "mp"}


def test_recall_bm25_respects_bank():
    ms = FakeMS([[{"id": "m1"}]])
    R._leg_bm25("q", user_id="u", bank="coding", as_of=AS_OF, ms=ms)
    assert "bank = %s" in ms.calls[0][0]
    assert ms.calls[0][1][1] == "coding"


def test_coding_memory_not_in_personal_recall():
    from tests._fakes import MockVectorStore

    vs = MockVectorStore()
    vs.create_collection("memories", 3)
    vs.upsert(
        "memories",
        "p-c",
        [0.1, 0.2, 0.3],
        {"memory_id": "only-coding", "user_id": "u", "bank": "coding"},
    )
    ms_coding = FakeMS(
        [
            [
                {
                    "id": "only-coding",
                    "content": "x",
                    "bank": "coding",
                    "valid_from": AS_OF,
                    "valid_until": None,
                }
            ],
            [{"evidence_id": "only-coding", "entity_ids": []}],
        ]
    )
    found = R.recall(
        user_id="u",
        bank="coding",
        query="q",
        retrieval_strategies=["semantic"],
        as_of=AS_OF,
        rerank=False,
        ms=ms_coding,
        embedder=FakeEmbedder(),
        vs=vs,
    )
    assert [m.id for m in found] == ["only-coding"]

    ms_personal = FakeMS([])
    empty = R.recall(
        user_id="u",
        bank="personal",
        query="q",
        retrieval_strategies=["semantic"],
        as_of=AS_OF,
        rerank=False,
        ms=ms_personal,
        embedder=FakeEmbedder(),
        vs=vs,
    )
    assert empty == []


def test_entity_edge_projection_uses_bank_graph(monkeypatch):
    seen: list[str | None] = []

    def _gs(bank=None):
        seen.append(bank)
        return None

    monkeypatch.setattr(config, "get_graph_store", _gs)
    ms = Mock()
    ms.fetch_one.return_value = {"id": "e1"}
    entity_edges.store_edge(
        user_id="u",
        bank="personal",
        src_entity_id="s",
        dst_entity_id="d",
        relation_type="RELATES_TO",
        ms=ms,
    )
    assert seen == ["personal"]


def test_ensure_tenant_index_idempotent():
    client = Mock()
    from adapters.qdrant_store import QdrantStore

    store = QdrantStore.__new__(QdrantStore)
    store._client = client
    store.ensure_tenant_payload_index("memories", "bank")
    store.ensure_tenant_payload_index("memories", "bank")
    assert client.create_payload_index.call_count == 2
    _second_exc = client.create_payload_index.call_args_list[1]
    # second call may swallow "already exists" — no raise from our method
    store.ensure_tenant_payload_index("memories", "bank")


def test_ensure_tenant_index_passes_is_tenant():
    from qdrant_client.models import KeywordIndexParams
    from qdrant_client.models import KeywordIndexType

    client = Mock()
    from adapters.qdrant_store import QdrantStore

    store = QdrantStore.__new__(QdrantStore)
    store._client = client
    store.ensure_tenant_payload_index("memories", "bank")
    _, kwargs = client.create_payload_index.call_args
    schema = kwargs["field_schema"]
    assert isinstance(schema, KeywordIndexParams)
    assert schema.is_tenant is True
    assert schema.type == KeywordIndexType.KEYWORD


def test_cross_bank_entity_name_visible(monkeypatch):
    from services import entities
    from services import mcp_write

    def fake_store(ents, **kwargs):
        return ["e-coding"]

    monkeypatch.setattr(mcp_write, "store_entities", fake_store)
    mcp_write.add_entity(user_id="u", bank="coding", name="Widget", entity_type="CONCEPT")

    class MS:
        def fetch_one(self, query, params=None):
            if "allows_shared" in (query or "").lower():
                return {"allows_shared": True}
            return None

        def fetch_all(self, query, params=None):
            if "FROM entities" in query:
                return [
                    {
                        "entity_id": "e-coding",
                        "name": "Widget",
                        "entity_type": "CONCEPT",
                        "mention_count": 1,
                        "scope": "private",
                    }
                ]
            return []

    monkeypatch.setattr(config, "get_metadata_store", lambda: MS())
    hits = entities.search_by_name("Widget", user_id="u", limit=5)
    assert hits and hits[0]["name"] == "Widget"
