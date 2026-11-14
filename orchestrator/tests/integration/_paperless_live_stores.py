# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Real-store wiring for the live paperless pipeline lane (LUM-309, Pass 2).

**This is the file the pipeline lane's honesty depends on**, and the reason is
not obvious from reading the tests it serves.

``orchestrator/tests/conftest.py`` carries an **autouse** ``_override_config``
fixture that writes ``MockVectorStore`` / ``MockMetadataStore`` /
``MockEmbedder`` into ``config._instances`` for *every* test under
``orchestrator/tests/`` — ``tests/integration/`` very much included. And
``_poll_paperless_source`` resolves its stores through ``config.get_*()``. So a
pipeline test placed in this tree and left alone would poll **real** paperless
and write into **mocks**: an expensive way to test fakes, wearing the costume of
an integration test. That is precisely the defect class this whole chunk exists
to eliminate (see LUM-748, where exactly that happened and went unnoticed).

So :func:`live_stores` displaces the mocks — and then **asserts that it did**.
It does not trust the arrangement. If ``conftest.py`` ever changes injection
order or stops clearing ``_instances`` on teardown, this lane fails loudly at
fixture setup naming the cause, rather than quietly reverting to testing mocks.

**This lane retrieves points by computed id, never by search.** The embedder
below therefore only has to produce a *storable* vector, not a meaningful one.
Any future assertion here about search relevance or ranking would be invalid —
the vectors are hashes, and they mean nothing.
"""

from __future__ import annotations

import hashlib
import math
import os

import pytest

import config

DOCUMENTS = "documents"

#: Matches nomic-embed-text, which is what the `documents` collection is
#: created with in production. The dimension only has to be self-consistent
#: here (fresh volume every run, per `down -v`), but matching production keeps
#: one fewer thing different between this lane and the real system.
_VECTOR_SIZE = 768


class _DeterministicEmbedder:
    """A test-local embedder producing non-zero, L2-normalised vectors.

    Why not reuse the ``MockEmbedder`` that ``conftest.py`` already ships: it
    returns ``[0.0] * 768``. The ``documents`` collection is created with
    ``Distance.COSINE``, and a zero vector has no direction — cosine
    normalisation on upsert is undefined, so Qdrant may reject it or store
    something unusable. This is not a hypothetical tidiness objection; it is the
    difference between the Qdrant half of this lane working and not.

    Deliberately hash-based and I/O-free so the embedder is never the reason the
    gate is slow, and so it needs no Ollama and no model pull.
    """

    def ping(self) -> bool:
        return True

    @property
    def vector_size(self) -> int:
        return _VECTOR_SIZE

    def embed(self, text: str) -> list[float]:
        # Expand sha256 into vector_size floats by re-hashing with a counter.
        raw = bytearray()
        counter = 0
        while len(raw) < _VECTOR_SIZE * 2:
            raw.extend(hashlib.sha256(f"{counter}:{text}".encode()).digest())
            counter += 1
        # Two bytes per component, mapped into [-1, 1).
        vals = [((raw[i * 2] << 8 | raw[i * 2 + 1]) / 32768.0) - 1.0 for i in range(_VECTOR_SIZE)]
        norm = math.sqrt(sum(v * v for v in vals))
        if norm == 0.0:  # pragma: no cover - unreachable for sha256 output
            vals[0] = 1.0
            norm = 1.0
        return [v / norm for v in vals]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [self.embed(t) for t in texts]


@pytest.fixture
def live_stores(monkeypatch):
    """Real Postgres + Qdrant + a storable embedder, installed over the autouse mocks.

    Returns ``(metadata_store, vector_store)``.
    """
    from adapters.postgres_store import PostgresStore
    from adapters.qdrant_store import QdrantStore
    from tests.integration._pgguard import require_postgres

    embedder = _DeterministicEmbedder()

    # The one non-obvious property of the embedder, asserted where it is built
    # rather than left to fail confusingly inside a Qdrant upsert.
    probe = embedder.embed("lum309 fixture probe")
    assert len(probe) == _VECTOR_SIZE
    assert any(v != 0.0 for v in probe), "a zero vector cannot be stored in a cosine collection"
    assert math.isclose(math.sqrt(sum(v * v for v in probe)), 1.0, rel_tol=1e-6)

    try:
        vs = QdrantStore(url=os.environ.get("QDRANT_URL", "http://127.0.0.1:6337"))
        ms = PostgresStore(
            host=os.environ.get("POSTGRES_HOST", "127.0.0.1"),
            port=int(os.environ.get("POSTGRES_PORT", "5434")),
            user=os.environ.get("POSTGRES_USER", "lumogis"),
            password=os.environ.get("POSTGRES_PASSWORD", "lumogis-dev"),
            dbname=os.environ.get("POSTGRES_DB", "lumogis"),
        )
        if not ms.ping():
            raise RuntimeError("postgres ping failed")
        # Startup code does not run in-process, so nothing else will have
        # created the collection.
        try:
            vs.count(DOCUMENTS)
        except Exception:
            vs.create_collection(DOCUMENTS, _VECTOR_SIZE)
            vs.count(DOCUMENTS)
    except Exception as exc:  # pragma: no cover - env-dependent
        require_postgres(exc, what="paperless-fixture Postgres/Qdrant")

    monkeypatch.setitem(config._instances, "vector_store", vs)
    monkeypatch.setitem(config._instances, "metadata_store", ms)
    monkeypatch.setitem(config._instances, "embedder", embedder)

    # Assert the displacement rather than assume it. See the module docstring:
    # the autouse mock injection is the failure mode that would otherwise turn
    # this whole lane into a slow way of testing fakes.
    resolved_ms = config.get_metadata_store()
    resolved_vs = config.get_vector_store()
    resolved_emb = config.get_embedder()
    assert isinstance(resolved_ms, PostgresStore), (
        f"config.get_metadata_store() returned {type(resolved_ms).__name__}, not PostgresStore. "
        "The autouse _override_config fixture in orchestrator/tests/conftest.py was not "
        "displaced — this lane would be polling real paperless and writing into mocks."
    )
    assert isinstance(resolved_vs, QdrantStore), (
        f"config.get_vector_store() returned {type(resolved_vs).__name__}, not QdrantStore. "
        "See above — the mock injection won."
    )
    assert resolved_emb is embedder, (
        "config.get_embedder() did not return the deterministic embedder; the "
        "zero-vector MockEmbedder would make every Qdrant assertion meaningless."
    )

    return ms, vs
