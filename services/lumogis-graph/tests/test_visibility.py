# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Unit tests for ``services/lumogis-graph/visibility.py`` (LUM-577 mirror)."""

from __future__ import annotations

import pytest
from auth import UserContext
from visibility import visible_cypher_fragment
from visibility import visible_filter
from visibility import visible_qdrant_filter

import config


class _AllowsSharedStore:
    def ping(self) -> bool:
        return True

    def close(self) -> None:
        pass

    def fetch_one(self, query: str, params: tuple | None = None):
        q = " ".join(query.split()).lower()
        if "allows_shared from users" in q:
            uid = (params or ())[0]
            row = self.users.get(uid)
            return dict(row) if row else None
        return None

    def fetch_all(self, query: str, params: tuple | None = None):
        return []


@pytest.fixture
def vis_ms(monkeypatch: pytest.MonkeyPatch) -> _AllowsSharedStore:
    store = _AllowsSharedStore()
    store.users = {}
    config._instances["metadata_store"] = store
    return store


def test_visible_filter_personal_only_member_excludes_shared(vis_ms):
    vis_ms.users["alice"] = {"allows_shared": False}
    user = UserContext(user_id="alice", is_authenticated=True, role="user")
    clause, params = visible_filter(user)
    assert clause == "((scope = 'personal' AND user_id = %s) OR scope = 'system')"
    assert params == ("alice",)


def test_visible_qdrant_filter_personal_only_excludes_shared_arm(vis_ms):
    vis_ms.users["alice"] = {"allows_shared": False}
    user = UserContext(user_id="alice", is_authenticated=True, role="user")
    f = visible_qdrant_filter(user)
    assert f == {
        "should": [
            {
                "must": [
                    {"key": "scope", "match": {"value": "personal"}},
                    {"key": "user_id", "match": {"value": "alice"}},
                ]
            },
            {"key": "scope", "match": {"value": "system"}},
        ]
    }


def test_visible_cypher_fragment_personal_only_excludes_shared(vis_ms):
    vis_ms.users["alice"] = {"allows_shared": False}
    user = UserContext(user_id="alice", is_authenticated=True, role="user")
    fragment, params = visible_cypher_fragment(user)
    assert fragment == ("((n.scope = 'personal' AND n.user_id = $vis_me) OR n.scope = 'system')")
    assert params == {"vis_me": "alice"}


def test_visible_filter_db_lookup_when_allows_shared_unset(vis_ms):
    vis_ms.users["bob"] = {"allows_shared": False}
    user = UserContext(user_id="bob", is_authenticated=True, role="user")
    clause, params = visible_filter(user)
    assert "scope = 'system'" in clause
    assert "shared" not in clause
    assert params == ("bob",)


def test_visible_filter_user_id_only_context_uses_db_allows_shared(vis_ms):
    """CONTEXT_BUILDING / viz paths pass only user_id — must not inherit default admin."""
    vis_ms.users["bob"] = {"allows_shared": False}
    user = UserContext(user_id="bob")
    clause, params = visible_filter(user)
    assert clause == "((scope = 'personal' AND user_id = %s) OR scope = 'system')"
    assert params == ("bob",)
