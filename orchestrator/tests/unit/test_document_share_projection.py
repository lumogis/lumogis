# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Unit tests for the LUM-157 shared-document content projection.

Exercises ``projection._project_file_chunks`` / ``_unproject_file_chunks``
against a fake vector store that faithfully models the adapter contract:
``scroll_collection(with_vectors=True)`` returns stored vectors, and
``delete_where`` honours the ``{"must": [{"key", "match"}]}`` clause shape —
so an empty ``must`` (match-all) deletes everything, catching the P0-2
regression (LUM-157 review R3).

These are port-level unit tests (no real Qdrant); the two-user retrieval and
migration paths are covered by the integration suite.
"""
from __future__ import annotations

import pytest

from services import projection


class FakeVectorStore:
    """Minimal in-memory Qdrant stand-in modelling the pieces projection uses."""

    def __init__(self):
        # id -> {"payload": {...}, "vector": [...]}
        self.points: dict[str, dict] = {}

    def add_personal_chunk(self, point_id, user_id, file_path, chunk_index, vector, text="t"):
        self.points[point_id] = {
            "payload": {
                "user_id": user_id,
                "file_path": file_path,
                "chunk_index": chunk_index,
                "text": text,
                "scope": "personal",
            },
            "vector": vector,
        }

    def scroll_collection(
        self, name, user_id=None, with_vectors=False, batch_size=100, file_path=None
    ):
        out = []
        for pid, pt in self.points.items():
            p = pt["payload"]
            if user_id is not None and p.get("user_id") != user_id:
                continue
            if file_path is not None and p.get("file_path") != file_path:
                continue
            out.append(
                {
                    "id": pid,
                    "payload": dict(p),
                    "vector": pt["vector"] if with_vectors else None,
                }
            )
        return out

    def upsert(self, collection, id, vector, payload):
        self.points[id] = {"payload": dict(payload), "vector": vector}

    def delete_where(self, collection, filter):
        # Faithfully model _build_filter: a must clause matches points where every
        # {key,match:{value}} holds; an EMPTY must matches ALL (the match-all bug).
        must = filter.get("must", []) if "should" not in filter else None
        if must is None:
            raise AssertionError("should-filters not expected in unshare cleanup")
        def matches(payload):
            for cond in must:
                if payload.get(cond["key"]) != cond["match"]["value"]:
                    return False
            return True  # empty must -> True for every point (match-all)
        for pid in [p for p, pt in self.points.items() if matches(pt["payload"])]:
            del self.points[pid]

    def count_where(self, collection, filter):
        must = filter.get("must", [])
        total = 0
        for pt in self.points.values():
            payload = pt["payload"]
            if all(payload.get(c["key"]) == c["match"]["value"] for c in must):
                total += 1
        return total


@pytest.fixture
def fake_vs(monkeypatch):
    vs = FakeVectorStore()
    monkeypatch.setattr(projection.config, "get_vector_store", lambda: vs)
    return vs


def test_project_file_chunks_reuses_vectors_to_shared_scope(fake_vs):
    fake_vs.add_personal_chunk("p0", "dad", "/m.pdf", 0, [0.1, 0.2], "chunk0")
    fake_vs.add_personal_chunk("p1", "dad", "/m.pdf", 1, [0.3, 0.4], "chunk1")
    # a different doc + a different user must be untouched
    fake_vs.add_personal_chunk("q0", "dad", "/other.pdf", 0, [0.9], "other")
    fake_vs.add_personal_chunk("m0", "mum", "/m.pdf", 0, [0.5], "mums")

    projected, failed = projection._project_file_chunks(
        {"id": 101, "user_id": "dad", "file_path": "/m.pdf"},
        target_scope="shared",
    )
    assert projected == 2
    assert failed == 0
    shared = [pt for pt in fake_vs.points.values() if pt["payload"]["scope"] == "shared"]
    assert len(shared) == 2
    for pt in shared:
        assert pt["payload"]["published_from"] == 101
        assert pt["payload"]["file_path"] == "/m.pdf"
        assert pt["payload"]["user_id"] == "dad"
        assert pt["vector"] in ([0.1, 0.2], [0.3, 0.4])  # REUSED, not re-embedded
    # other doc / other user untouched
    assert fake_vs.points["q0"]["payload"]["scope"] == "personal"
    assert fake_vs.points["m0"]["payload"]["scope"] == "personal"


def test_unproject_deletes_only_the_shared_copies_not_the_collection(fake_vs):
    """P0-2 regression guard: scoped delete, never match-all."""
    fake_vs.add_personal_chunk("p0", "dad", "/m.pdf", 0, [0.1], "c0")
    fake_vs.add_personal_chunk("p1", "dad", "/m.pdf", 1, [0.2], "c1")
    fake_vs.add_personal_chunk("q0", "dad", "/other.pdf", 0, [0.9], "other")
    projection._project_file_chunks({"id": 101, "user_id": "dad", "file_path": "/m.pdf"},
                                    target_scope="shared")
    assert sum(pt["payload"]["scope"] == "shared" for pt in fake_vs.points.values()) == 2

    projection._unproject_file_chunks(101, "dad", "shared")

    # shared copies gone; ALL personal originals (this doc + other doc) intact
    assert all(pt["payload"]["scope"] == "personal" for pt in fake_vs.points.values())
    assert {p for p in fake_vs.points} == {"p0", "p1", "q0"}


def test_project_skips_non_personal_and_missing_ids(fake_vs):
    # no owner/file_path -> no-op
    assert projection._project_file_chunks({"id": 5, "user_id": "", "file_path": ""},
                                           target_scope="shared") == (0, 0)


def test_project_reports_failed_chunks_as_partial(fake_vs, monkeypatch):
    """A per-chunk upsert failure is counted (failed>0) so the job can go partial."""
    fake_vs.add_personal_chunk("p0", "dad", "/m.pdf", 0, [0.1], "c0")
    fake_vs.add_personal_chunk("p1", "dad", "/m.pdf", 1, [0.2], "c1")

    calls = {"n": 0}
    real_upsert = fake_vs.upsert

    def flaky_upsert(collection, id, vector, payload):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("transient qdrant failure")
        return real_upsert(collection, id, vector, payload)

    monkeypatch.setattr(fake_vs, "upsert", flaky_upsert)

    projected, failed = projection._project_file_chunks(
        {"id": 101, "user_id": "dad", "file_path": "/m.pdf"},
        target_scope="shared",
    )
    assert projected == 1
    assert failed == 1


# ---------------------------------------------------------------------------
# Finding 5 — chunk projection is gated to target_scope == 'shared'
# ---------------------------------------------------------------------------


def test_project_file_chunks_noop_for_non_shared_scope(fake_vs):
    """system-scope publish must NOT mirror personal document content."""
    fake_vs.add_personal_chunk("p0", "dad", "/m.pdf", 0, [0.1], "c0")
    assert projection._project_file_chunks(
        {"id": 101, "user_id": "dad", "file_path": "/m.pdf"},
        target_scope="system",
    ) == (0, 0)
    # nothing projected — the only point is the untouched personal source chunk
    assert all(pt["payload"]["scope"] == "personal" for pt in fake_vs.points.values())


# ---------------------------------------------------------------------------
# Finding 4 — a missing/non-int chunk_index must never collide on one point id
# ---------------------------------------------------------------------------


def _add_raw_chunk(fake_vs, pid, payload, vector):
    fake_vs.points[pid] = {"payload": dict(payload), "vector": vector}


def test_project_skips_missing_chunk_index_no_collision(fake_vs):
    """Two chunks both lacking chunk_index would collapse onto ``101:None``.

    Without the guard the second upsert silently overwrites the first (one
    shared point instead of two). The guard skips both and counts them failed,
    so the job reports an honest partial rather than losing content silently.
    """
    _add_raw_chunk(
        fake_vs,
        "p0",
        {"user_id": "dad", "file_path": "/m.pdf", "chunk_index": None,
         "text": "a", "scope": "personal"},
        [0.1],
    )
    _add_raw_chunk(
        fake_vs,
        "p1",
        {"user_id": "dad", "file_path": "/m.pdf", "text": "b", "scope": "personal"},
        [0.2],
    )
    projected, failed = projection._project_file_chunks(
        {"id": 101, "user_id": "dad", "file_path": "/m.pdf"},
        target_scope="shared",
    )
    assert projected == 0
    assert failed == 2
    assert all(pt["payload"]["scope"] == "personal" for pt in fake_vs.points.values())


def test_project_mixed_valid_and_missing_chunk_index(fake_vs):
    fake_vs.add_personal_chunk("p0", "dad", "/m.pdf", 0, [0.1], "c0")  # valid
    _add_raw_chunk(
        fake_vs,
        "p1",
        {"user_id": "dad", "file_path": "/m.pdf", "chunk_index": None,
         "text": "b", "scope": "personal"},
        [0.2],
    )
    projected, failed = projection._project_file_chunks(
        {"id": 101, "user_id": "dad", "file_path": "/m.pdf"},
        target_scope="shared",
    )
    assert projected == 1
    assert failed == 1
    shared = [pt for pt in fake_vs.points.values() if pt["payload"]["scope"] == "shared"]
    assert len(shared) == 1
    assert shared[0]["payload"]["chunk_index"] == 0


# ---------------------------------------------------------------------------
# Finding 6 — project_file_with_status enforces owner == actor
# ---------------------------------------------------------------------------


def test_project_file_owner_must_match_actor(fake_vs):
    """A source row owned by someone other than the actor is refused up front."""
    from auth import UserContext

    with pytest.raises(ValueError):
        projection.project_file_with_status(
            {"id": 101, "user_id": "dad", "file_path": "/m.pdf"},
            target_scope="shared",
            actor=UserContext(user_id="mum", is_authenticated=True),
        )
    # nothing was projected before the guard tripped
    assert not any(
        pt["payload"].get("scope") == "shared" for pt in fake_vs.points.values()
    )


# ---------------------------------------------------------------------------
# Finding 3 — re-share after a re-ingest with fewer chunks leaves no stale points
# ---------------------------------------------------------------------------


def test_reshare_after_fewer_chunks_leaves_no_stale(fake_vs, monkeypatch):
    """share(3 chunks) → re-ingest to 2 → re-share → exactly 2 shared points.

    ``project_file_with_status`` must unproject-then-project so the shared set
    exactly mirrors the current source chunks; the removed index-2 chunk must
    not survive as a retrievable shared orphan.
    """
    from auth import UserContext

    # Stub the Postgres projection-row insert (unit test has no metadata store);
    # the finding is about the Qdrant chunk set, which uses the real code path.
    monkeypatch.setattr(
        projection,
        "_project_file_row",
        lambda src, *, target_scope, actor: {"id": 999, "scope": target_scope},
    )
    actor = UserContext(user_id="dad", is_authenticated=True)
    src = {"id": 101, "user_id": "dad", "file_path": "/m.pdf"}

    fake_vs.add_personal_chunk("p0", "dad", "/m.pdf", 0, [0.1], "c0")
    fake_vs.add_personal_chunk("p1", "dad", "/m.pdf", 1, [0.2], "c1")
    fake_vs.add_personal_chunk("p2", "dad", "/m.pdf", 2, [0.3], "c2")

    _row, projected, failed = projection.project_file_with_status(
        src, target_scope="shared", actor=actor
    )
    assert (projected, failed) == (3, 0)
    assert sum(pt["payload"]["scope"] == "shared" for pt in fake_vs.points.values()) == 3

    # Simulate a re-ingest that produced FEWER chunks: the old index-2 personal
    # chunk no longer exists (ingest wipes+rewrites the personal chunk set).
    del fake_vs.points["p2"]

    _row2, projected2, failed2 = projection.project_file_with_status(
        src, target_scope="shared", actor=actor
    )
    assert (projected2, failed2) == (2, 0)
    shared = [pt for pt in fake_vs.points.values() if pt["payload"]["scope"] == "shared"]
    assert len(shared) == 2, "stale shared orphan for the removed chunk index"
    assert sorted(pt["payload"]["chunk_index"] for pt in shared) == [0, 1]
