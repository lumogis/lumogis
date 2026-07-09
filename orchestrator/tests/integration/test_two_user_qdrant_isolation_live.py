# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Live two-user Qdrant semantic-search isolation integration test (LUM-307).

Proves ``visible_qdrant_filter`` + real Qdrant ``Filter(should=...)`` ANN behaviour:
no cross-user personal chunk leakage and household-union shared visibility for both
members. LUM-587 extends coverage to members with ``allows_shared=False`` who must
not see shared-scope chunks. LUM-588 proves the production ``semantic_search()``
path (live embedder → Qdrant filter → results) has no cross-user personal leakage.
Complements the mock-based harness in
``test_two_user_isolation.py`` (unit translation is in
``test_qdrant_store_filter_build.py``).

Skips cleanly when a real Qdrant-backed store is not reachable (plain host unit run).
Primary CI gate: ``make compose-test`` (or targeted pytest inside the orchestrator
container).
"""

from __future__ import annotations

import uuid

import pytest
from auth import UserContext
from visibility import visible_qdrant_filter

import config

pytestmark = pytest.mark.integration

COLLECTION = "documents"


def _vector_dim() -> int:
    try:
        return int(config.get_embedder().vector_size)
    except Exception:
        return 768


def _run_vector_index(dim: int) -> int:
    """Per-test-run unique dense index so only this test's points score ~1.0."""
    return 1 + (int(uuid.uuid4().hex[:8], 16) % max(1, dim - 1))


def _one_hot(dim: int, idx: int) -> list[float]:
    vec = [0.0] * dim
    vec[idx % dim] = 1.0
    return vec


def _query_vector(dim: int, idx: int) -> list[float]:
    return _one_hot(dim, idx)


def _seed_personal_chunks(
    vs,
    user_id: str,
    file_path: str,
    vector_idx: int,
    n_chunks: int = 2,
) -> None:
    dim = _vector_dim()
    for i in range(n_chunks):
        idx = vector_idx if i == 0 else (vector_idx + i) % dim
        vs.upsert(
            collection=COLLECTION,
            id=str(uuid.uuid4()),
            vector=_one_hot(dim, idx),
            payload={
                "user_id": user_id,
                "file_path": file_path,
                "chunk_index": i,
                "text": f"personal chunk {i} {uuid.uuid4().hex}",
                "scope": "personal",
            },
        )


def _seed_shared_chunk(
    vs,
    owner_user_id: str,
    file_path: str,
    vector_idx: int,
    text: str,
) -> None:
    dim = _vector_dim()
    vs.upsert(
        collection=COLLECTION,
        id=str(uuid.uuid4()),
        vector=_one_hot(dim, vector_idx),
        payload={
            "scope": "shared",
            "file_path": file_path,
            "chunk_index": 0,
            "text": text,
            "user_id": owner_user_id,
        },
    )


def _cleanup(vs, *file_paths: str) -> None:
    for fp in file_paths:
        try:
            vs.delete_where(
                COLLECTION,
                {"must": [{"key": "file_path", "match": {"value": fp}}]},
            )
        except Exception:
            pass


def _seed_personal_chunk_embedded(vs, user_id: str, file_path: str, text: str) -> None:
    """Seed one personal chunk with a live-embedder vector (LUM-588)."""
    try:
        vector = config.get_embedder().embed(text)
    except Exception as exc:  # pragma: no cover - env-dependent
        pytest.skip(f"live embedder not reachable: {exc}")
    vs.upsert(
        collection=COLLECTION,
        id=str(uuid.uuid4()),
        vector=vector,
        payload={
            "user_id": user_id,
            "file_path": file_path,
            "chunk_index": 0,
            "text": text,
            "scope": "personal",
        },
    )


def _paths_from_search_results(results) -> set[str]:
    return {r.file_path for r in results}


@pytest.fixture
def live_stores(monkeypatch):
    """Yield (metadata_store, vector_store) wired to the REAL stack, or skip."""
    import os

    try:
        from adapters.postgres_store import PostgresStore
        from adapters.qdrant_store import QdrantStore

        vs = QdrantStore(url=os.environ.get("QDRANT_URL", "http://qdrant:6333"))
        ms = PostgresStore(
            host=os.environ.get("POSTGRES_HOST", "postgres"),
            port=int(os.environ.get("POSTGRES_PORT", "5432")),
            user=os.environ.get("POSTGRES_USER", "lumogis"),
            password=os.environ.get("POSTGRES_PASSWORD", "lumogis-dev"),
            dbname=os.environ.get("POSTGRES_DB", "lumogis"),
        )
        if not ms.ping():
            raise RuntimeError("postgres ping failed")
        try:
            vs.count(COLLECTION)
        except Exception:
            vs.create_collection(COLLECTION, _vector_dim())
            vs.count(COLLECTION)
    except Exception as exc:  # pragma: no cover - env-dependent
        pytest.skip(f"real stack not reachable: {exc}")

    monkeypatch.setitem(config._instances, "vector_store", vs)
    monkeypatch.setitem(config._instances, "metadata_store", ms)
    return ms, vs


def _paths_in_results(results: list[dict]) -> set[str]:
    return {(r.get("payload") or {}).get("file_path") for r in results}


def _personal_hits(results: list[dict], user_id: str) -> list[dict]:
    return [
        r
        for r in results
        if (r.get("payload") or {}).get("scope") == "personal"
        and (r.get("payload") or {}).get("user_id") == user_id
    ]


def test_live_alice_and_bob_no_cross_user_qdrant_leakage(live_stores):
    _ms, vs = live_stores
    alice = f"itest-alice-{uuid.uuid4().hex[:8]}"
    bob = f"itest-bob-{uuid.uuid4().hex[:8]}"
    file_path_a = f"/uploads/{alice}/lum307-{uuid.uuid4().hex[:8]}.md"
    file_path_b = f"/uploads/{bob}/lum307-{uuid.uuid4().hex[:8]}.md"
    dim = _vector_dim()
    vector_idx = _run_vector_index(dim)
    alice_ctx = UserContext(user_id=alice, is_authenticated=True, role="user", allows_shared=True)
    bob_ctx = UserContext(user_id=bob, is_authenticated=True, role="user", allows_shared=True)
    try:
        _seed_personal_chunks(vs, alice, file_path_a, vector_idx)
        _seed_personal_chunks(vs, bob, file_path_b, vector_idx)

        query = _query_vector(dim, vector_idx)

        alice_results = vs.search(
            COLLECTION,
            query,
            limit=50,
            threshold=0.0,
            filter=visible_qdrant_filter(alice_ctx),
        )
        alice_paths = _paths_in_results(alice_results)
        assert file_path_a in alice_paths, (
            f"Alice positive control failed: {file_path_a} not in {alice_paths}"
        )
        assert file_path_b not in alice_paths, (
            f"Alice saw Bob's personal doc: {file_path_b} in {alice_results}"
        )
        bob_personal_in_alice = _personal_hits(alice_results, bob)
        assert not bob_personal_in_alice, (
            f"Alice saw Bob personal payloads: {bob_personal_in_alice}"
        )

        bob_results = vs.search(
            COLLECTION,
            query,
            limit=50,
            threshold=0.0,
            filter=visible_qdrant_filter(bob_ctx),
        )
        bob_paths = _paths_in_results(bob_results)
        assert file_path_b in bob_paths, (
            f"Bob positive control failed: {file_path_b} not in {bob_paths}"
        )
        assert file_path_a not in bob_paths, (
            f"Bob saw Alice's personal doc: {file_path_a} in {bob_results}"
        )
        alice_personal_in_bob = _personal_hits(bob_results, alice)
        assert not alice_personal_in_bob, (
            f"Bob saw Alice personal payloads: {alice_personal_in_bob}"
        )
    finally:
        _cleanup(vs, file_path_a, file_path_b)


def test_live_household_union_returns_shared_for_both_users(live_stores):
    _ms, vs = live_stores
    alice = f"itest-alice-{uuid.uuid4().hex[:8]}"
    bob = f"itest-bob-{uuid.uuid4().hex[:8]}"
    file_path_a = f"/uploads/{alice}/lum307-{uuid.uuid4().hex[:8]}.md"
    file_path_b = f"/uploads/{bob}/lum307-{uuid.uuid4().hex[:8]}.md"
    file_path_shared = f"/uploads/shared/lum307-{uuid.uuid4().hex[:8]}.md"
    dim = _vector_dim()
    vector_idx = _run_vector_index(dim)
    alice_ctx = UserContext(user_id=alice, is_authenticated=True, role="user", allows_shared=True)
    bob_ctx = UserContext(user_id=bob, is_authenticated=True, role="user", allows_shared=True)
    try:
        _seed_personal_chunks(vs, alice, file_path_a, vector_idx)
        _seed_personal_chunks(vs, bob, file_path_b, vector_idx)
        _seed_shared_chunk(
            vs,
            alice,
            file_path_shared,
            vector_idx,
            f"shared household doc {uuid.uuid4().hex}",
        )

        query = _query_vector(dim, vector_idx)

        for label, ctx, own_path, other_path in (
            ("Alice", alice_ctx, file_path_a, file_path_b),
            ("Bob", bob_ctx, file_path_b, file_path_a),
        ):
            results = vs.search(
                COLLECTION,
                query,
                limit=50,
                threshold=0.0,
                filter=visible_qdrant_filter(ctx),
            )
            paths = _paths_in_results(results)
            assert file_path_shared in paths, (
                f"{label} union positive control failed: {file_path_shared} not in {paths}"
            )
            shared_hits = [
                r
                for r in results
                if (r.get("payload") or {}).get("file_path") == file_path_shared
                and (r.get("payload") or {}).get("scope") == "shared"
            ]
            assert shared_hits, f"{label} did not retrieve shared chunk at {file_path_shared}"
            assert own_path in paths, (
                f"{label} personal positive control failed: {own_path} not in {paths}"
            )
            other_personal = [
                r
                for r in results
                if (r.get("payload") or {}).get("scope") == "personal"
                and (r.get("payload") or {}).get("file_path") == other_path
            ]
            assert not other_personal, (
                f"{label} saw other's personal path {other_path}: {other_personal}"
            )
    finally:
        _cleanup(vs, file_path_a, file_path_b, file_path_shared)


def test_live_personal_only_member_excludes_shared_chunks(live_stores):
    """LUM-587: member with allows_shared=False must not retrieve shared chunks."""
    _ms, vs = live_stores
    alice = f"itest-alice-{uuid.uuid4().hex[:8]}"
    bob = f"itest-bob-{uuid.uuid4().hex[:8]}"
    file_path_a = f"/uploads/{alice}/lum587-{uuid.uuid4().hex[:8]}.md"
    file_path_b = f"/uploads/{bob}/lum587-{uuid.uuid4().hex[:8]}.md"
    file_path_shared = f"/uploads/shared/lum587-{uuid.uuid4().hex[:8]}.md"
    dim = _vector_dim()
    vector_idx = _run_vector_index(dim)
    alice_ctx = UserContext(user_id=alice, is_authenticated=True, role="user", allows_shared=True)
    bob_ctx = UserContext(user_id=bob, is_authenticated=True, role="user", allows_shared=False)
    try:
        _seed_personal_chunks(vs, alice, file_path_a, vector_idx)
        _seed_personal_chunks(vs, bob, file_path_b, vector_idx)
        _seed_shared_chunk(
            vs,
            alice,
            file_path_shared,
            vector_idx,
            f"shared household doc {uuid.uuid4().hex}",
        )

        query = _query_vector(dim, vector_idx)

        bob_results = vs.search(
            COLLECTION,
            query,
            limit=50,
            threshold=0.0,
            filter=visible_qdrant_filter(bob_ctx),
        )
        bob_paths = _paths_in_results(bob_results)
        assert file_path_b in bob_paths, (
            f"Bob personal positive control failed: {file_path_b} not in {bob_paths}"
        )
        assert file_path_shared not in bob_paths, (
            f"Bob (allows_shared=False) saw shared doc: {file_path_shared} in {bob_results}"
        )
        shared_hits_for_bob = [
            r for r in bob_results if (r.get("payload") or {}).get("file_path") == file_path_shared
        ]
        assert not shared_hits_for_bob, (
            f"Bob retrieved shared payloads despite opt-out: {shared_hits_for_bob}"
        )
        assert file_path_a not in bob_paths, (
            f"Bob saw Alice's personal doc: {file_path_a} in {bob_results}"
        )

        alice_results = vs.search(
            COLLECTION,
            query,
            limit=50,
            threshold=0.0,
            filter=visible_qdrant_filter(alice_ctx),
        )
        alice_paths = _paths_in_results(alice_results)
        assert file_path_shared in alice_paths, (
            f"Alice union positive control failed: {file_path_shared} not in {alice_paths}"
        )
        assert file_path_a in alice_paths, (
            f"Alice personal positive control failed: {file_path_a} not in {alice_paths}"
        )
        assert file_path_b not in alice_paths, (
            f"Alice saw Bob's personal doc: {file_path_b} in {alice_results}"
        )
    finally:
        _cleanup(vs, file_path_a, file_path_b, file_path_shared)


def test_live_semantic_search_no_cross_user_leakage(live_stores, monkeypatch):
    """LUM-588: semantic_search with live embedder respects per-user visibility."""
    _ms, vs = live_stores
    monkeypatch.setattr(config, "get_reranker", lambda: None)

    from services.search import semantic_search

    alice = f"itest-alice-{uuid.uuid4().hex[:8]}"
    bob = f"itest-bob-{uuid.uuid4().hex[:8]}"
    file_path_a = f"/uploads/{alice}/lum588-{uuid.uuid4().hex[:8]}.md"
    file_path_b = f"/uploads/{bob}/lum588-{uuid.uuid4().hex[:8]}.md"
    marker_a = uuid.uuid4().hex
    marker_b = uuid.uuid4().hex
    alice_text = f"LUM588 Alice retrieval code {marker_a}; sphinx of black quartz, judge my vow."
    bob_text = f"LUM588 Bob retrieval code {marker_b}; waltz bad nymph for quick jigs vex."
    try:
        _seed_personal_chunk_embedded(vs, alice, file_path_a, alice_text)
        _seed_personal_chunk_embedded(vs, bob, file_path_b, bob_text)

        alice_results = semantic_search(alice_text, limit=10, user_id=alice)
        alice_paths = _paths_from_search_results(alice_results)
        assert file_path_a in alice_paths, (
            f"Alice positive control failed: {file_path_a} not in {alice_paths}"
        )
        assert file_path_b not in alice_paths, (
            f"Alice semantic_search leaked Bob doc: {file_path_b} in {alice_results}"
        )
        assert not any(marker_b in (r.chunk_text or "") for r in alice_results), (
            f"Alice saw Bob marker in semantic_search results: {alice_results}"
        )

        bob_results = semantic_search(bob_text, limit=10, user_id=bob)
        bob_paths = _paths_from_search_results(bob_results)
        assert file_path_b in bob_paths, (
            f"Bob positive control failed: {file_path_b} not in {bob_paths}"
        )
        assert file_path_a not in bob_paths, (
            f"Bob semantic_search leaked Alice doc: {file_path_a} in {bob_results}"
        )
        assert not any(marker_a in (r.chunk_text or "") for r in bob_results), (
            f"Bob saw Alice marker in semantic_search results: {bob_results}"
        )
    finally:
        _cleanup(vs, file_path_a, file_path_b)
