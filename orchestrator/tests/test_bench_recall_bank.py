# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""The LUM-542 synthetic recall bank is deterministic and shaped like real data.

No backend: this checks the generator only. The benchmark itself runs with
``make bench-recall`` (tier-2 throwaway databases), never in ``make test``.
"""

from __future__ import annotations

import numpy as np
from benchmarks import recall_bank as rb


def test_same_seed_same_bank_and_queries():
    a = rb.generate_bank(300, seed=7)
    b = rb.generate_bank(300, seed=7)
    assert a.fingerprint() == b.fingerprint()
    qa = rb.generate_queries(a, 50, seed=7)
    qb = rb.generate_queries(b, 50, seed=7)
    assert [q.text for q in qa] == [q.text for q in qb]
    assert all(np.array_equal(x.vector, y.vector) for x, y in zip(qa, qb))


def test_different_seed_or_stream_differs():
    base = rb.generate_bank(100, seed=7).fingerprint()
    assert rb.generate_bank(100, seed=8).fingerprint() != base
    assert rb.generate_bank(100, seed=7, stream=1).fingerprint() != base


def test_bank_shape():
    bank = rb.generate_bank(2000)
    assert len(bank.memories) == 2000
    assert len({m.id for m in bank.memories}) == 2000
    assert bank.vectors.shape == (2000, rb.EMBED_DIM)
    assert np.allclose(np.linalg.norm(bank.vectors, axis=1), 1.0, atol=1e-4)

    words = sorted(len(m.content.split()) for m in bank.memories)
    assert words[0] >= 6 and words[-1] <= 340  # head + appended names/identifiers
    assert 15 <= words[len(words) // 2] <= 45  # median ~28
    assert words[int(0.95 * len(words))] >= 60  # long tail

    topics = {m.topic for m in bank.memories}
    assert len(topics) == len(rb.TOPICS)
    archived = sum(1 for m in bank.memories if m.valid_until is not None)
    assert 0.02 * 2000 <= archived <= 0.07 * 2000
    assert all(m.metadata["source"] and m.tags for m in bank.memories)
    assert sum(1 for m in bank.memories if m.identifiers) > 0.15 * 2000


def test_graph_is_consistent():
    bank = rb.generate_bank(1500)
    entity_ids = {e.entity_id for e in bank.entities}
    memory_ids = {m.id for m in bank.memories}
    assert len(bank.entities) >= 0.3 * 1500 - 1
    assert len({e.name.lower() for e in bank.entities}) == len(bank.entities)
    keys = {(e.src_entity_id, e.dst_entity_id, e.relation_type) for e in bank.edges}
    assert len(keys) == len(bank.edges)  # the table's UNIQUE key
    for edge in bank.edges:
        assert edge.src_entity_id in entity_ids and edge.dst_entity_id in entity_ids
        assert edge.evidence_id in memory_ids
    # Memory text names its linked entities, so the graph seed ILIKE can find them.
    names = {e.entity_id: e.name for e in bank.entities}
    for m in bank.memories[:200]:
        for eid in m.entity_ids:
            assert names[eid] in m.content


def test_query_mix_covers_every_shape():
    bank = rb.generate_bank(1000)
    queries = rb.generate_queries(bank, 300)
    shapes = {q.shape for q in queries}
    assert shapes == {s for s, _ in rb.QUERY_MIX}
    idents = {i for m in bank.memories for i in m.identifiers}
    assert all(q.text in idents for q in queries if q.shape == "identifier")
    assert all(q.vector.shape == (rb.EMBED_DIM,) for q in queries)


def test_no_graph_bank():
    bank = rb.generate_bank(50, graph=False, bank="personal")
    assert bank.entities == [] and bank.edges == []
    assert all(m.bank == "personal" and m.entity_ids == [] for m in bank.memories)
