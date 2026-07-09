# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""LUM-585 — "Shared by {member}" attribution: label precedence + the
non-owner-only, single-lookup, never-full-email derivation on get_document.
"""

from __future__ import annotations

from datetime import datetime
from datetime import timezone

import pytest
from auth import UserContext
from services.documents import get_document
from services.documents import list_documents

import config
from services import users as users_svc

NOW = datetime(2026, 6, 1, tzinfo=timezone.utc)


class _AttrStore:
    """Answers the users + document queries attribution touches; counts the
    users lookups so the no-N+1 property is directly assertable."""

    def __init__(self):
        self.users: dict[str, dict] = {}
        self.file_rows: list[dict] = []
        self.user_query_count = 0

    def add_user(self, uid, email, display_name=None):
        self.users[uid] = {
            "id": uid,
            "email": email,
            "password_hash": "x",
            "role": "user",
            "disabled": False,
            "created_at": NOW,
            "display_name": display_name,
        }

    def add_doc(self, **row):
        base = {
            "file_type": ".pdf",
            "file_hash": "h",
            "chunk_count": 2,
            "scope": "personal",
            "updated_at": NOW,
            "published_from": None,
            "entity_count": 0,
        }
        base.update(row)
        self.file_rows.append(base)

    def fetch_one(self, query, params=None):
        q = " ".join(query.split()).lower()
        p = params or ()
        if q.startswith("select * from users where id"):
            self.user_query_count += 1
            return self.users.get(p[0])
        if "from file_index fi" in q and "where fi.id" in q:
            for r in self.file_rows:
                if r["id"] == p[0]:
                    return dict(r)
            return None
        if "select user_id from file_index where id = %s and scope = 'personal'" in q:
            for r in self.file_rows:
                if r["id"] == p[0] and r.get("scope") == "personal":
                    return {"user_id": r["user_id"]}
            return None
        return None

    def fetch_all(self, query, params=None):
        q = " ".join(query.split()).lower()
        p = params or ()
        if "from file_index fi" in q:  # list_documents main query
            caller = p[-2]
            out = []
            for r in self.file_rows:
                if r.get("published_from") is not None and r.get("user_id") == caller:
                    continue  # owner projection collapsed
                out.append(dict(r))
            return out
        return []


@pytest.fixture
def store(monkeypatch):
    s = _AttrStore()
    config._instances["metadata_store"] = s
    yield s
    config._instances.pop("metadata_store", None)


def _user(uid):
    # allows_shared set so the visibility filter never issues its own users query.
    return UserContext(user_id=uid, is_authenticated=True, role="user", allows_shared=True)


def _shared_doc(store, display_name=None):
    store.add_user("alice", "alice@home.lan", display_name=display_name)
    store.add_doc(id=10, file_path="/report.pdf", user_id="alice", scope="personal")
    store.add_doc(
        id=100, file_path="/report.pdf", user_id="alice", scope="shared", published_from=10
    )


# --- display_label_for precedence + fallbacks ----------------------------


def test_label_uses_display_name_when_set(store):
    store.add_user("alice", "alice@home.lan", display_name="Alex")
    assert users_svc.display_label_for("alice") == "Alex"


def test_label_falls_back_to_local_part(store):
    store.add_user("alice", "tommstar@pm.me", display_name=None)
    assert users_svc.display_label_for("alice") == "tommstar"


def test_label_whitespace_display_name_falls_back(store):
    store.add_user("alice", "alice@home.lan", display_name="   ")
    assert users_svc.display_label_for("alice") == "alice"


def test_label_none_for_missing_user(store):
    assert users_svc.display_label_for("ghost") is None


def test_label_none_for_default_user(store):
    assert users_svc.display_label_for("default") is None


def test_label_never_contains_full_email(store):
    store.add_user("alice", "alice@home.lan")
    assert "@" not in (users_svc.display_label_for("alice") or "")


# --- set_display_name normalization --------------------------------------


def test_set_display_name_normalizes_blank_to_null(store):
    captured = {}
    store.execute = lambda q, params=None: captured.update(params=params)  # type: ignore[attr-defined]
    store.add_user("alice", "alice@home.lan")
    users_svc.set_display_name("alice", "   ")
    assert captured["params"][0] is None  # whitespace -> NULL
    users_svc.set_display_name("alice", "")
    assert captured["params"][0] is None
    users_svc.set_display_name("alice", "  Alex  ")
    assert captured["params"][0] == "Alex"  # trimmed, stored


def test_display_name_round_trips_through_both_projectors(store):
    """A stored value must flow row -> _row_to_internal -> _to_admin_view together
    (guards both wirings in one flow, not just each half)."""
    import routes.admin_users as admin_users

    store.add_user("alice", "alice@home.lan")
    # A real UPDATE against the in-memory rows, then read back through the real
    # get_user_by_id (-> _row_to_internal) and project to the admin view.
    store.execute = lambda q, params=None: store.users["alice"].__setitem__(  # type: ignore[attr-defined]
        "display_name", params[0]
    )
    users_svc.set_display_name("alice", "Alex")
    internal = users_svc.get_user_by_id("alice")
    assert internal.display_name == "Alex"  # _row_to_internal wiring
    assert admin_users._to_admin_view(internal).display_name == "Alex"  # _to_admin_view wiring


# --- get_document attribution derivation ---------------------------------


def test_non_owner_sees_shared_by_label(store):
    _shared_doc(store, display_name="Alex")
    doc = get_document(_user("bob"), 100)
    assert doc.is_owner is False
    assert doc.share_status == "shared"
    assert doc.shared_by == "Alex"


def test_non_owner_shared_by_falls_back_to_local_part(store):
    _shared_doc(store, display_name=None)
    doc = get_document(_user("bob"), 100)
    assert doc.shared_by == "alice"
    assert "@" not in doc.shared_by


def test_owner_view_has_no_shared_by(store):
    _shared_doc(store, display_name="Alex")
    doc = get_document(_user("alice"), 100)  # alice owns the projection
    assert doc.is_owner is True
    assert doc.shared_by is None


def test_personal_item_has_no_shared_by(store):
    store.add_user("alice", "alice@home.lan", display_name="Alex")
    store.add_doc(id=11, file_path="/private.pdf", user_id="alice", scope="personal")
    doc = get_document(_user("alice"), 11)
    assert doc.share_status == "personal"
    assert doc.shared_by is None


def test_get_document_resolves_shared_by_in_one_lookup(store):
    _shared_doc(store, display_name="Alex")
    get_document(_user("bob"), 100)
    assert store.user_query_count == 1  # exactly one users lookup (no N+1)


def test_list_documents_issues_no_user_query(store):
    _shared_doc(store, display_name="Alex")
    rows = list_documents(_user("bob"), limit=50)
    assert rows and all(r.shared_by is None for r in rows)  # attribution is detail-only
    assert store.user_query_count == 0  # the list never resolves labels
