# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Unit tests for services/document_purge.py (LUM-160 / LUM-500 / LUM-501)."""

from __future__ import annotations

from unittest.mock import MagicMock
from unittest.mock import patch

import pytest
from services.document_purge import DocumentNotFoundError
from services.document_purge import purge_document
from services.point_ids import document_chunk_point_id

import config


class _FileIndexStore:
    def __init__(self) -> None:
        self.rows: dict[int, dict] = {}
        self.entity_relations: list[tuple] = []
        self.entities: dict[str, dict] = {}  # entity_id -> row
        self._next_id = 1
        self.executed: list[tuple[str, tuple | None]] = []
        self._fail_qdrant = False
        # LUM-500 / LUM-501: tombstone state
        self.purged_documents: dict[tuple[str, int], dict] = {}

    def ping(self) -> bool:
        return True

    def close(self) -> None:
        pass

    @staticmethod
    def transaction():
        from contextlib import contextmanager

        @contextmanager
        def _tx():
            yield

        return _tx()

    def fetch_one(self, query: str, params: tuple | None = None):
        q = " ".join(query.split()).lower()
        p = params or ()
        if "from file_index" in q and "scope = 'personal'" in q and "id = %s" in q:
            doc_id, uid = p[0], p[1]
            row = self.rows.get(int(doc_id))
            if row and row["user_id"] == uid and row.get("scope") == "personal":
                return dict(row)
            return None
        if "from purged_documents" in q:
            uid, doc_id = p[0], p[1]
            return self.purged_documents.get((uid, doc_id))
        return None

    def fetch_all(self, query: str, params: tuple | None = None) -> list[dict]:
        """Simulate the orphan-entity detection query (LUM-501)."""
        q = " ".join(query.split()).lower()
        p = params or ()
        if "from entities" in q and "entity_id in" in q:
            # params: (user_id, file_path, user_id, user_id, file_path)
            uid, fp = p[0], p[1]
            # Collect entity_ids referenced in entity_relations for this file_path + user.
            # entity_relations tuples may be (fp, uid) or (fp, uid, eid).
            referenced = {
                er[2]
                for er in self.entity_relations
                if len(er) >= 3 and er[0] == fp and er[1] == uid
            }
            # Keep entities that have no other relation.
            orphans = []
            for eid, erow in self.entities.items():
                if erow["user_id"] != uid or erow.get("scope") != "personal":
                    continue
                if eid not in referenced:
                    continue
                # Check no other relation for a different file_path.
                other = any(
                    len(er) >= 3 and er[1] == uid and er[2] == eid and er[0] != fp
                    for er in self.entity_relations
                )
                if not other:
                    orphans.append({"entity_id": eid})
            return orphans
        return []

    def execute(self, query: str, params: tuple | None = None) -> None:
        self.executed.append((query, params))
        q = " ".join(query.split()).lower()
        p = params or ()
        if "delete from file_index where published_from" in q:
            src = int(p[0])
            for rid in list(self.rows):
                if self.rows[rid].get("published_from") == src:
                    del self.rows[rid]
        elif "delete from entity_relations" in q:
            fp, uid = p[0], p[1]
            self.entity_relations = [
                r for r in self.entity_relations if not (r[0] == fp and r[1] == uid)
            ]
        elif "delete from entities" in q and "entity_id = any(" in q:
            entity_ids, uid = list(p[0]), p[1]
            for eid in entity_ids:
                row = self.entities.get(eid)
                if row and row["user_id"] == uid and row.get("scope") == "personal":
                    del self.entities[eid]
        elif "delete from entities" in q and "published_from = any(" in q:
            for src in list(p[0]):
                for eid, row in list(self.entities.items()):
                    if row.get("published_from") == src and row.get("scope") == "shared":
                        del self.entities[eid]
        elif "delete from file_index where id" in q:
            doc_id, uid = int(p[0]), p[1]
            row = self.rows.get(doc_id)
            if row and row["user_id"] == uid and row.get("scope") == "personal":
                del self.rows[doc_id]
        elif "insert into purged_documents" in q:
            uid, doc_id, fp, cc = p[0], p[1], p[2], p[3]
            orphan_ids = p[4] if len(p) > 4 else "[]"
            import json as _json

            key = (uid, doc_id)
            if key not in self.purged_documents:
                self.purged_documents[key] = {
                    "user_id": uid,
                    "document_id": doc_id,
                    "file_path": fp,
                    "chunk_count": cc,
                    "qdrant_deleted": False,
                    "graph_deleted": False,
                    "qdrant_entities_deleted": False,
                    "orphan_entity_ids": (
                        _json.loads(orphan_ids) if isinstance(orphan_ids, str) else orphan_ids
                    ),
                    "errors": [],
                    "resolved_at": None,
                }
        elif "update purged_documents" in q:
            qdrant_ok, graph_ok, ent_ok = bool(p[0]), bool(p[1]), bool(p[2])
            uid, doc_id = p[4], p[5]
            key = (uid, doc_id)
            if key in self.purged_documents:
                self.purged_documents[key]["qdrant_deleted"] = qdrant_ok
                self.purged_documents[key]["graph_deleted"] = graph_ok
                self.purged_documents[key]["qdrant_entities_deleted"] = ent_ok
                if qdrant_ok and graph_ok and ent_ok:
                    self.purged_documents[key]["resolved_at"] = "NOW()"


@pytest.fixture
def file_ms(monkeypatch: pytest.MonkeyPatch) -> _FileIndexStore:
    store = _FileIndexStore()
    config._instances["metadata_store"] = store
    return store


# ---------------------------------------------------------------------------
# Original LUM-160 tests (unchanged behaviour)
# ---------------------------------------------------------------------------


def test_purge_document_qdrant_enumeration(file_ms, mock_vector_store, monkeypatch):
    monkeypatch.setitem(
        config._instances, config._graph_store_cache_key("personal"), None
    )
    uid = "alice"
    fp = "/uploads/alice/doc.pdf"
    file_ms.rows[1] = {
        "id": 1,
        "file_path": fp,
        "chunk_count": 2,
        "user_id": uid,
        "scope": "personal",
    }
    for i in range(2):
        pid = document_chunk_point_id(uid, fp, i)
        mock_vector_store.upsert(
            collection="documents",
            id=pid,
            vector=[0.0] * 768,
            payload={"user_id": uid, "file_path": fp},
        )
    result = purge_document(user_id=uid, document_id=1)
    assert result.postgres_deleted is True
    assert result.qdrant_deleted is True
    assert mock_vector_store.count("documents") == 0


def test_purge_document_deletes_sparse_chunk_indices(file_ms, mock_vector_store, monkeypatch):
    """block_ingest can skip early chunks; purge must not rely on contiguous 0..chunk_count-1."""
    monkeypatch.setitem(
        config._instances, config._graph_store_cache_key("personal"), None
    )
    uid = "alice"
    fp = "/uploads/alice/sparse.pdf"
    file_ms.rows[1] = {
        "id": 1,
        "file_path": fp,
        "chunk_count": 1,
        "user_id": uid,
        "scope": "personal",
    }
    sparse_pid = document_chunk_point_id(uid, fp, 2)
    mock_vector_store.upsert(
        collection="documents",
        id=sparse_pid,
        vector=[0.0] * 768,
        payload={"user_id": uid, "file_path": fp, "chunk_index": 2},
    )
    result = purge_document(user_id=uid, document_id=1)
    assert result.qdrant_deleted is True
    assert mock_vector_store.count("documents") == 0


def test_purge_document_partial_when_qdrant_fails(file_ms, mock_vector_store, monkeypatch):
    monkeypatch.setitem(
        config._instances, config._graph_store_cache_key("personal"), None
    )
    file_ms.rows[1] = {
        "id": 1,
        "file_path": "/x.pdf",
        "chunk_count": 1,
        "user_id": "alice",
        "scope": "personal",
    }

    def _boom():
        raise RuntimeError("qdrant down")

    mock_vector_store.delete = MagicMock(side_effect=_boom)
    result = purge_document(user_id="alice", document_id=1)
    assert result.postgres_deleted is True
    assert result.partial is True


def test_purge_entity_relations_uses_file_path(file_ms, mock_vector_store, monkeypatch):
    monkeypatch.setitem(
        config._instances, config._graph_store_cache_key("personal"), None
    )
    fp = "/docs/a.txt"
    file_ms.rows[3] = {
        "id": 3,
        "file_path": fp,
        "chunk_count": 0,
        "user_id": "alice",
        "scope": "personal",
    }
    file_ms.entity_relations.append((fp, "alice"))
    purge_document(user_id="alice", document_id=3)
    rel_delete = next(
        (p for q, p in file_ms.executed if "entity_relations" in q.lower()),
        None,
    )
    assert rel_delete == (fp, "alice")
    assert file_ms.entity_relations == []


def test_delete_document_cascades_projection_rows(file_ms, mock_vector_store, monkeypatch):
    monkeypatch.setitem(
        config._instances, config._graph_store_cache_key("personal"), None
    )
    file_ms.rows[1] = {
        "id": 1,
        "file_path": "/personal.pdf",
        "chunk_count": 0,
        "user_id": "alice",
        "scope": "personal",
    }
    file_ms.rows[2] = {
        "id": 2,
        "file_path": "/personal.pdf",
        "chunk_count": 0,
        "user_id": "alice",
        "scope": "shared",
        "published_from": 1,
    }
    purge_document(user_id="alice", document_id=1)
    assert 1 not in file_ms.rows
    assert 2 not in file_ms.rows


def test_purge_raises_when_not_found(file_ms, mock_vector_store, monkeypatch):
    monkeypatch.setitem(
        config._instances, config._graph_store_cache_key("personal"), None
    )
    with pytest.raises(DocumentNotFoundError):
        purge_document(user_id="alice", document_id=99)


def test_delete_no_entities_delete_when_no_orphans(file_ms, mock_vector_store, monkeypatch):
    """No orphan entities detected (entities dict empty) → no DELETE FROM entities."""
    monkeypatch.setitem(
        config._instances, config._graph_store_cache_key("personal"), None
    )
    fp = "/docs/orphan.pdf"
    file_ms.rows[4] = {
        "id": 4,
        "file_path": fp,
        "chunk_count": 0,
        "user_id": "alice",
        "scope": "personal",
    }
    file_ms.entity_relations.append((fp, "alice"))
    purge_document(user_id="alice", document_id=4)
    rel_delete = next(
        (p for q, p in file_ms.executed if "entity_relations" in q.lower()),
        None,
    )
    assert rel_delete == (fp, "alice")
    entity_delete = [q for q, _ in file_ms.executed if "delete from entities" in q.lower()]
    assert entity_delete == []


# ---------------------------------------------------------------------------
# LUM-500 tombstone tests
# ---------------------------------------------------------------------------


def test_purge_inserts_tombstone_on_postgres_success(file_ms, mock_vector_store, monkeypatch):
    monkeypatch.setitem(
        config._instances, config._graph_store_cache_key("personal"), None
    )
    file_ms.rows[10] = {
        "id": 10,
        "file_path": "/docs/report.pdf",
        "chunk_count": 3,
        "user_id": "alice",
        "scope": "personal",
    }
    purge_document(user_id="alice", document_id=10)
    tombstone = file_ms.purged_documents.get(("alice", 10))
    assert tombstone is not None
    assert tombstone["file_path"] == "/docs/report.pdf"
    assert tombstone["chunk_count"] == 3


def test_purge_tombstone_resolved_on_full_success(file_ms, mock_vector_store, monkeypatch):
    monkeypatch.setitem(
        config._instances, config._graph_store_cache_key("personal"), None
    )
    file_ms.rows[11] = {
        "id": 11,
        "file_path": "/docs/full.pdf",
        "chunk_count": 0,
        "user_id": "alice",
        "scope": "personal",
    }
    purge_document(user_id="alice", document_id=11)
    tombstone = file_ms.purged_documents.get(("alice", 11))
    assert tombstone is not None
    assert tombstone["qdrant_deleted"] is True
    assert tombstone["graph_deleted"] is True
    assert tombstone["resolved_at"] is not None


def test_purge_partial_tombstone_stays_unresolved(file_ms, mock_vector_store, monkeypatch):
    monkeypatch.setitem(
        config._instances, config._graph_store_cache_key("personal"), None
    )
    file_ms.rows[12] = {
        "id": 12,
        "file_path": "/docs/partial.pdf",
        "chunk_count": 1,
        "user_id": "alice",
        "scope": "personal",
    }
    mock_vector_store.delete = MagicMock(side_effect=RuntimeError("qdrant down"))
    purge_document(user_id="alice", document_id=12)
    tombstone = file_ms.purged_documents.get(("alice", 12))
    assert tombstone is not None
    assert tombstone["qdrant_deleted"] is False
    assert tombstone["resolved_at"] is None


def test_purge_partial_emits_structured_log_event(file_ms, mock_vector_store, monkeypatch):
    monkeypatch.setitem(
        config._instances, config._graph_store_cache_key("personal"), None
    )
    file_ms.rows[13] = {
        "id": 13,
        "file_path": "/docs/event.pdf",
        "chunk_count": 1,
        "user_id": "alice",
        "scope": "personal",
    }
    mock_vector_store.delete = MagicMock(side_effect=RuntimeError("qdrant down"))

    import services.document_purge as dp_mod

    with patch.object(dp_mod._log, "warning") as mock_warn:
        purge_document(user_id="alice", document_id=13)

    calls = [
        c for c in mock_warn.call_args_list if c.args and "document_purge_partial" in str(c.args[0])
    ]
    assert calls, "document_purge_partial log event not emitted"
    extra = calls[0].kwargs.get("extra", {})
    assert extra.get("event") == "document_purge_partial"
    assert extra.get("document_id") == 13


def test_purge_retry_via_tombstone(file_ms, mock_vector_store, monkeypatch):
    monkeypatch.setitem(
        config._instances, config._graph_store_cache_key("personal"), None
    )
    # First call: Qdrant fails → partial tombstone.
    file_ms.rows[14] = {
        "id": 14,
        "file_path": "/docs/retry.pdf",
        "chunk_count": 1,
        "user_id": "alice",
        "scope": "personal",
    }
    mock_vector_store.delete = MagicMock(side_effect=RuntimeError("qdrant down"))
    result1 = purge_document(user_id="alice", document_id=14)
    assert result1.partial is True

    # file_index row is gone; tombstone is partial.
    assert 14 not in file_ms.rows
    tombstone = file_ms.purged_documents.get(("alice", 14))
    assert tombstone is not None and tombstone["qdrant_deleted"] is False

    # Second call: Qdrant succeeds this time.
    mock_vector_store.delete = MagicMock(return_value=None)
    result2 = purge_document(user_id="alice", document_id=14)
    assert result2.postgres_deleted is True
    assert result2.qdrant_deleted is True
    assert result2.partial is False

    tombstone2 = file_ms.purged_documents.get(("alice", 14))
    assert tombstone2["qdrant_deleted"] is True
    assert tombstone2["resolved_at"] is not None


def test_purge_idempotent_on_resolved_tombstone(file_ms, mock_vector_store, monkeypatch):
    monkeypatch.setitem(
        config._instances, config._graph_store_cache_key("personal"), None
    )
    # Pre-seed a resolved tombstone; no file_index row.
    file_ms.purged_documents[("alice", 20)] = {
        "file_path": "/docs/done.pdf",
        "chunk_count": 2,
        "qdrant_deleted": True,
        "graph_deleted": True,
        "qdrant_entities_deleted": True,
        "orphan_entity_ids": [],
        "resolved_at": "2026-06-18T00:00:00Z",
    }
    result = purge_document(user_id="alice", document_id=20)
    assert result.postgres_deleted is True
    assert result.qdrant_deleted is True
    assert result.graph_deleted is True
    assert result.partial is False
    # No store arm calls — nothing was deleted from Qdrant.
    assert mock_vector_store.count("documents") == 0


def test_purge_raises_not_found_without_tombstone(file_ms, mock_vector_store, monkeypatch):
    monkeypatch.setitem(
        config._instances, config._graph_store_cache_key("personal"), None
    )
    # No file_index row, no tombstone.
    with pytest.raises(DocumentNotFoundError):
        purge_document(user_id="alice", document_id=99)


# ---------------------------------------------------------------------------
# LUM-501 orphan-entity GC tests
# ---------------------------------------------------------------------------


def test_entity_gc_removes_orphan_entities(file_ms, mock_vector_store, monkeypatch):
    """Deleting a doc whose entity has no other evidence removes entity from Postgres + Qdrant."""
    monkeypatch.setitem(
        config._instances, config._graph_store_cache_key("personal"), None
    )
    fp = "/docs/gc.pdf"
    eid = "ent-aaa-111"
    file_ms.rows[30] = {
        "id": 30,
        "file_path": fp,
        "chunk_count": 0,
        "user_id": "alice",
        "scope": "personal",
    }
    file_ms.entities[eid] = {"entity_id": eid, "user_id": "alice", "scope": "personal"}
    file_ms.entity_relations.append((fp, "alice", eid))

    mock_vector_store.delete_where = MagicMock()
    result = purge_document(user_id="alice", document_id=30)

    assert result.postgres_deleted is True
    assert result.qdrant_entities_deleted is True
    assert eid not in file_ms.entities
    # Document chunk sweep + LUM-157 shared-chunk (published_from) sweep on the
    # documents collection + two entity filtered deletes (personal + projection).
    assert mock_vector_store.delete_where.call_count == 4
    entity_calls = [
        c
        for c in mock_vector_store.delete_where.call_args_list
        if c.kwargs["collection"] == "entities"
    ]
    assert len(entity_calls) == 2
    keys = set()
    for c in entity_calls:
        must = c.kwargs["filter"]["must"]
        assert {"key": "user_id", "match": {"value": "alice"}} in must
        keys.update(m["key"] for m in must)
    assert "entity_id" in keys
    assert "published_from" in keys


def test_entity_gc_skips_non_orphan_entities(file_ms, mock_vector_store, monkeypatch):
    """Entity with evidence from another document is NOT deleted."""
    monkeypatch.setitem(
        config._instances, config._graph_store_cache_key("personal"), None
    )
    fp = "/docs/partial-gc.pdf"
    fp2 = "/docs/other.pdf"
    eid = "ent-bbb-222"
    file_ms.rows[31] = {
        "id": 31,
        "file_path": fp,
        "chunk_count": 0,
        "user_id": "alice",
        "scope": "personal",
    }
    file_ms.entities[eid] = {"entity_id": eid, "user_id": "alice", "scope": "personal"}
    # Entity has two relations — one for the deleted doc, one for another.
    file_ms.entity_relations.append((fp, "alice", eid))
    file_ms.entity_relations.append((fp2, "alice", eid))

    mock_vector_store.delete_where = MagicMock()
    purge_document(user_id="alice", document_id=31)

    assert eid in file_ms.entities
    entity_calls = [
        c
        for c in mock_vector_store.delete_where.call_args_list
        if c.kwargs["collection"] == "entities"
    ]
    assert entity_calls == []


def test_entity_gc_sweeps_shared_projection_points(file_ms, mock_vector_store, monkeypatch):
    """The projection-sweep filter carries the orphan ids under published_from (LUM-501)."""
    monkeypatch.setitem(
        config._instances, config._graph_store_cache_key("personal"), None
    )
    fp = "/docs/published.pdf"
    eid = "ent-eee-555"
    file_ms.rows[35] = {
        "id": 35,
        "file_path": fp,
        "chunk_count": 0,
        "user_id": "alice",
        "scope": "personal",
    }
    file_ms.entities[eid] = {"entity_id": eid, "user_id": "alice", "scope": "personal"}
    file_ms.entity_relations.append((fp, "alice", eid))

    mock_vector_store.delete_where = MagicMock()
    purge_document(user_id="alice", document_id=35)

    # Restrict to the entities collection: LUM-157 also issues a
    # published_from delete on the documents collection (shared-chunk sweep),
    # which is not the entity-projection sweep under test here.
    projection_calls = [
        c
        for c in mock_vector_store.delete_where.call_args_list
        if c.kwargs["collection"] == "entities"
        and any(m["key"] == "published_from" for m in c.kwargs["filter"]["must"])
    ]
    assert len(projection_calls) == 1
    pf = next(
        m for m in projection_calls[0].kwargs["filter"]["must"] if m["key"] == "published_from"
    )
    assert pf["match"]["any"] == [eid]


def test_entity_gc_empty_when_no_entities(file_ms, mock_vector_store, monkeypatch):
    """Doc with no entities → entity Qdrant arm succeeds trivially; no entity delete_where."""
    monkeypatch.setitem(
        config._instances, config._graph_store_cache_key("personal"), None
    )
    file_ms.rows[32] = {
        "id": 32,
        "file_path": "/docs/noentity.pdf",
        "chunk_count": 0,
        "user_id": "alice",
        "scope": "personal",
    }
    mock_vector_store.delete_where = MagicMock()
    result = purge_document(user_id="alice", document_id=32)

    assert result.qdrant_entities_deleted is True
    entity_calls = [
        c
        for c in mock_vector_store.delete_where.call_args_list
        if c.kwargs["collection"] == "entities"
    ]
    assert entity_calls == []


def test_entity_gc_partial_when_qdrant_entities_fails(file_ms, mock_vector_store, monkeypatch):
    """Qdrant entity arm failure → partial; tombstone records qdrant_entities_deleted=False."""
    monkeypatch.setitem(
        config._instances, config._graph_store_cache_key("personal"), None
    )
    fp = "/docs/entfail.pdf"
    eid = "ent-ccc-333"
    file_ms.rows[33] = {
        "id": 33,
        "file_path": fp,
        "chunk_count": 0,
        "user_id": "alice",
        "scope": "personal",
    }
    file_ms.entities[eid] = {"entity_id": eid, "user_id": "alice", "scope": "personal"}
    file_ms.entity_relations.append((fp, "alice", eid))

    mock_vector_store.delete_where = MagicMock(side_effect=RuntimeError("entities collection down"))
    result = purge_document(user_id="alice", document_id=33)

    assert result.postgres_deleted is True
    assert result.qdrant_entities_deleted is False
    assert result.partial is True

    tombstone = file_ms.purged_documents.get(("alice", 33))
    assert tombstone is not None
    assert tombstone["qdrant_entities_deleted"] is False
    assert tombstone["resolved_at"] is None


def test_entity_gc_log_event_emitted(file_ms, mock_vector_store, monkeypatch):
    """document_entity_gc info log emitted when orphan entities are removed."""
    monkeypatch.setitem(
        config._instances, config._graph_store_cache_key("personal"), None
    )
    fp = "/docs/gclog.pdf"
    eid = "ent-ddd-444"
    file_ms.rows[34] = {
        "id": 34,
        "file_path": fp,
        "chunk_count": 0,
        "user_id": "alice",
        "scope": "personal",
    }
    file_ms.entities[eid] = {"entity_id": eid, "user_id": "alice", "scope": "personal"}
    file_ms.entity_relations.append((fp, "alice", eid))
    mock_vector_store.delete_where = MagicMock()

    import services.document_purge as dp_mod

    with patch.object(dp_mod._log, "info") as mock_info:
        purge_document(user_id="alice", document_id=34)

    calls = [
        c for c in mock_info.call_args_list if c.args and "document_entity_gc" in str(c.args[0])
    ]
    assert calls, "document_entity_gc log event not emitted"
    extra = calls[0].kwargs.get("extra", {})
    assert extra.get("event") == "document_entity_gc"
    assert extra.get("orphan_count") == 1
