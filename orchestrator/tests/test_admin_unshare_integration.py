# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""LUM-584 — admin_unshare driven through the REAL projection teardown.

The service unit tests spy the registry's ``unproject`` to isolate the
governance seams. This module instead runs ``admin_unshare`` end to end
through the *real* ``services.projection.unproject_note`` (UUID pk) and
``unproject_file`` (INTEGER pk + chunk delete_where) against a faithful
in-memory store + vector store — so the actual DELETE-by-``published_from``
SQL, the real Qdrant point / chunk teardown, and the post-teardown
``count_where`` verification are all exercised together (review finding P2:
the real SQL/teardown path was previously never run through admin_unshare).
"""

from __future__ import annotations

import pytest
from auth import UserContext

import config
from services import admin_unshare as svc
from services import projection as proj

ADMIN = UserContext(user_id="carol-admin", role="admin", is_authenticated=True)
OWNER = "bob-uid"


class _ProjStore:
    """In-memory store answering exactly the queries admin_unshare + the real
    ``unproject_*`` issue: the shared-owner lookup, the DELETE ... RETURNING
    teardown, and the audit insert.
    """

    def __init__(self):
        self.rows: dict[str, list[dict]] = {"notes": [], "file_index": []}
        self.audit: list[tuple] = []

    def add_shared(self, table, published_from, pk_col, pk_val):
        self.rows[table].append(
            {
                pk_col: pk_val,
                "published_from": published_from,
                "scope": "shared",
                "user_id": OWNER,
            }
        )

    def fetch_one(self, query, params=None):
        q = " ".join(query.split()).lower()
        if q.startswith("insert into audit_log"):
            self.audit.append(params)
            return {"id": len(self.audit)}
        if q.startswith("select user_id from") and "published_from = %s and scope = 'shared'" in q:
            table = q.split("from ")[1].split(" where")[0].strip()
            for r in self.rows.get(table, []):
                if str(r["published_from"]) == str(params[0]) and r["scope"] == "shared":
                    return {"user_id": r["user_id"]}
            return None
        if q.startswith("delete from notes") and "returning note_id" in q:
            return self._delete("notes", params, "note_id")
        if q.startswith("delete from file_index") and "returning id, user_id" in q:
            return self._delete("file_index", params, "id", extra="user_id")
        return None

    def _delete(self, table, params, pk_col, extra=None):
        published_from, scope = params[0], params[1]
        deleted = None
        keep = []
        for r in self.rows[table]:
            if str(r["published_from"]) == str(published_from) and r["scope"] == scope:
                deleted = r
            else:
                keep.append(r)
        self.rows[table] = keep
        if deleted is None:
            return None
        out = {pk_col: deleted[pk_col]}
        if extra:
            out[extra] = deleted.get(extra)
        return out

    def fetch_all(self, *a, **k):
        return []

    def execute(self, *a, **k):
        return None


class _VS:
    """Vector store modelling delete (single id), delete_where, and count_where."""

    def __init__(self):
        self.points: dict[str, dict] = {}

    def upsert(self, collection, id, vector, payload):
        self.points[id] = {"collection": collection, "payload": payload}

    def delete(self, collection, id):
        self.points.pop(id, None)

    def delete_where(self, collection, filter):
        must = filter.get("must", [])
        for pid in [
            p
            for p, pt in self.points.items()
            if pt["collection"] == collection
            and all(pt["payload"].get(c["key"]) == c["match"]["value"] for c in must)
        ]:
            del self.points[pid]

    def count_where(self, collection, filter):
        must = filter.get("must", [])
        return sum(
            1
            for pt in self.points.values()
            if pt["collection"] == collection
            and all(pt["payload"].get(c["key"]) == c["match"]["value"] for c in must)
        )


@pytest.fixture
def wiring(monkeypatch):
    store = _ProjStore()
    vs = _VS()
    monkeypatch.setattr(config, "get_metadata_store", lambda: store)
    monkeypatch.setattr(config, "get_vector_store", lambda: vs)
    return store, vs


def test_admin_unshare_notes_through_real_unproject(wiring):
    store, vs = wiring
    src = "note-src-1"
    store.add_shared("notes", src, "note_id", proj.projection_pk("notes", src, "shared"))
    point_id = proj.projection_point_id("conversations", src, "shared")
    vs.upsert("conversations", point_id, [0.0], {"published_from": src, "scope": "shared"})

    result = svc.admin_unshare(actor=ADMIN, resource="notes", pk=src)

    assert result["unshared"] is True and result["source_owner_id"] == OWNER
    assert store.rows["notes"] == []  # real DELETE ... RETURNING removed the row
    assert point_id not in vs.points  # real _qdrant_delete_safe removed the point
    assert len(store.audit) == 1


def test_admin_unshare_files_through_real_unproject_and_chunks(wiring):
    store, vs = wiring
    src = 7  # INTEGER pk
    store.add_shared("file_index", src, "id", proj.projection_pk("file_index", str(src), "shared"))
    # A shared document chunk keyed by INT published_from + owner + scope, as
    # project_file writes it; _unproject_file_chunks must clear it.
    vs.upsert(
        "documents",
        "chunk-0",
        [0.0],
        {"published_from": src, "user_id": OWNER, "scope": "shared"},
    )

    result = svc.admin_unshare(actor=ADMIN, resource="files", pk=str(src))

    assert result["unshared"] is True
    assert store.rows["file_index"] == []
    assert "chunk-0" not in vs.points  # real chunk delete_where cleared it
    assert len(store.audit) == 1
