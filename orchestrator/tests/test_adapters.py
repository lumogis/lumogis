# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Adapter smoke tests: basic operations succeed with mocks."""

from tests.conftest import MockEmbedder
from tests.conftest import MockMetadataStore
from tests.conftest import MockVectorStore


def test_vector_store_ping():
    vs = MockVectorStore()
    assert vs.ping() is True


def test_vector_store_crud():
    vs = MockVectorStore()
    vs.create_collection("test", 768)
    assert vs.count("test") == 0

    vs.upsert("test", "doc-1", [0.1] * 768, {"title": "hello"})
    assert vs.count("test") == 1

    results = vs.search("test", [0.1] * 768, limit=5, threshold=0.5)
    assert len(results) == 1
    assert results[0]["id"] == "doc-1"

    vs.delete("test", "doc-1")
    assert vs.count("test") == 0


def test_metadata_store_ping():
    ms = MockMetadataStore()
    assert ms.ping() is True


def test_metadata_store_fetch_returns_empty():
    ms = MockMetadataStore()
    assert ms.fetch_one("SELECT 1") is None
    assert ms.fetch_all("SELECT 1") == []


def test_embedder_ping():
    emb = MockEmbedder()
    assert emb.ping() is True


def test_embedder_vector_size():
    emb = MockEmbedder()
    assert emb.vector_size == 768


def test_embedder_embed():
    emb = MockEmbedder()
    vec = emb.embed("hello")
    assert len(vec) == 768


def test_embedder_embed_batch():
    emb = MockEmbedder()
    vecs = emb.embed_batch(["a", "b"])
    assert len(vecs) == 2
    assert all(len(v) == 768 for v in vecs)


def test_reranker_resolve_device_forces_cpu_on_macos(monkeypatch):
    """BGE reranker pins CPU on macOS (default MPS OOMs; appliance is CPU-only)."""
    from adapters.bge_reranker import _resolve_device

    monkeypatch.setattr("adapters.bge_reranker.sys.platform", "darwin")
    monkeypatch.delenv("RERANKER_DEVICE", raising=False)
    assert _resolve_device(None) == "cpu"


def test_reranker_resolve_device_auto_off_macos(monkeypatch):
    """Off macOS, leave device selection to sentence-transformers (CUDA Docker keeps GPU)."""
    from adapters.bge_reranker import _resolve_device

    monkeypatch.setattr("adapters.bge_reranker.sys.platform", "linux")
    monkeypatch.delenv("RERANKER_DEVICE", raising=False)
    assert _resolve_device(None) is None


def test_reranker_resolve_device_env_and_explicit_win(monkeypatch):
    """Explicit arg and RERANKER_DEVICE override the macOS default."""
    from adapters.bge_reranker import _resolve_device

    monkeypatch.setattr("adapters.bge_reranker.sys.platform", "darwin")
    monkeypatch.setenv("RERANKER_DEVICE", "cuda")
    assert _resolve_device(None) == "cuda"
    assert _resolve_device("cpu") == "cpu"
