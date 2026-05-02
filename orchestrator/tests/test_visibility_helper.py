# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Unit tests for ``orchestrator/visibility.py``.

Locks the canonical SQL clauses, Qdrant filter dicts, and Cypher
fragments returned by every helper. The 13 retrieval surfaces in
plan §6 all rely on these literals, so any drift here is a drift
across the entire scope contract — these tests are the single
source of truth for the helper output.
"""
from __future__ import annotations

import pytest

from auth import UserContext
from visibility import (
    DEFAULT_SCOPE,
    admin_unfiltered_cypher_fragment,
    admin_unfiltered_filter,
    admin_unfiltered_qdrant_filter,
    visible_cypher_fragment,
    visible_filter,
    visible_qdrant_filter,
)


@pytest.fixture
def user():
    return UserContext(user_id="alice", is_authenticated=True, role="user")


@pytest.fixture
def admin():
    return UserContext(user_id="adam", is_authenticated=True, role="admin")


def test_default_scope_is_personal():
    assert DEFAULT_SCOPE == "personal"


# ─── Postgres ────────────────────────────────────────────────────────────────


def test_visible_filter_default_household_union(user):
    clause, params = visible_filter(user)
    assert clause == (
        "((scope = 'personal' AND user_id = %s) OR scope IN ('shared','system'))"
    )
    assert params == ("alice",)


def test_visible_filter_personal_only(user):
    clause, params = visible_filter(user, scope_filter="personal")
    assert clause == "(scope = 'personal' AND user_id = %s)"
    assert params == ("alice",)


def test_visible_filter_shared_only(user):
    clause, params = visible_filter(user, scope_filter="shared")
    assert clause == "(scope = 'shared')"
    assert params == ()


def test_visible_filter_system_only(user):
    clause, params = visible_filter(user, scope_filter="system")
    assert clause == "(scope = 'system')"
    assert params == ()


def test_visible_filter_admin_using_personal_only_sees_self(admin):
    """ARBITRATE-R1-ADDENDUM (#4): an admin requesting ?scope=personal MUST
    see only their own personal rows, NOT cross-user personal data.
    The visible_filter helper does not branch on role, so this invariant
    holds by construction."""
    clause, params = visible_filter(admin, scope_filter="personal")
    assert "user_id = %s" in clause
    assert params == ("adam",)


def test_visible_filter_rejects_unknown_scope(user):
    with pytest.raises(ValueError):
        visible_filter(user, scope_filter="public")
    with pytest.raises(ValueError):
        visible_filter(user, scope_filter="")


def test_admin_unfiltered_filter_defaults_to_true():
    clause, params = admin_unfiltered_filter()
    assert clause == "(TRUE)"
    assert params == ()


def test_admin_unfiltered_filter_with_scope():
    clause, params = admin_unfiltered_filter(scope_filter="shared")
    assert clause == "(scope = %s)"
    assert params == ("shared",)


# ─── Qdrant ──────────────────────────────────────────────────────────────────


def test_visible_qdrant_filter_default_household_union(user):
    f = visible_qdrant_filter(user)
    assert f == {
        "should": [
            {
                "must": [
                    {"key": "scope", "match": {"value": "personal"}},
                    {"key": "user_id", "match": {"value": "alice"}},
                ]
            },
            {"key": "scope", "match": {"any": ["shared", "system"]}},
        ]
    }


def test_visible_qdrant_filter_personal_only(user):
    f = visible_qdrant_filter(user, scope_filter="personal")
    assert f == {
        "must": [
            {"key": "scope", "match": {"value": "personal"}},
            {"key": "user_id", "match": {"value": "alice"}},
        ]
    }


def test_visible_qdrant_filter_shared_only(user):
    f = visible_qdrant_filter(user, scope_filter="shared")
    assert f == {"must": [{"key": "scope", "match": {"value": "shared"}}]}


def test_visible_qdrant_filter_system_only(user):
    f = visible_qdrant_filter(user, scope_filter="system")
    assert f == {"must": [{"key": "scope", "match": {"value": "system"}}]}


def test_admin_unfiltered_qdrant_filter_default_returns_none():
    assert admin_unfiltered_qdrant_filter() is None


def test_admin_unfiltered_qdrant_filter_with_scope():
    f = admin_unfiltered_qdrant_filter(scope_filter="shared")
    assert f == {"must": [{"key": "scope", "match": {"value": "shared"}}]}


# ─── Cypher ──────────────────────────────────────────────────────────────────


def test_visible_cypher_fragment_default_household_union(user):
    fragment, params = visible_cypher_fragment(user)
    assert fragment == (
        "((n.scope = 'personal' AND n.user_id = $vis_me) "
        "OR n.scope IN ['shared','system'])"
    )
    assert params == {"vis_me": "alice"}


def test_visible_cypher_fragment_custom_alias(user):
    fragment, params = visible_cypher_fragment(user, alias="e")
    assert fragment == (
        "((e.scope = 'personal' AND e.user_id = $vis_me) "
        "OR e.scope IN ['shared','system'])"
    )
    assert params == {"vis_me": "alice"}


def test_visible_cypher_fragment_personal_only(user):
    fragment, params = visible_cypher_fragment(user, scope_filter="personal")
    assert fragment == "(n.scope = 'personal' AND n.user_id = $vis_me)"
    assert params == {"vis_me": "alice"}


def test_visible_cypher_fragment_shared_only(user):
    fragment, params = visible_cypher_fragment(user, scope_filter="shared")
    assert fragment == "(n.scope = 'shared')"
    assert params == {}


def test_visible_cypher_fragment_system_only(user):
    fragment, params = visible_cypher_fragment(user, scope_filter="system")
    assert fragment == "(n.scope = 'system')"
    assert params == {}


def test_admin_unfiltered_cypher_fragment_default():
    fragment, params = admin_unfiltered_cypher_fragment()
    assert fragment == "(TRUE)"
    assert params == {}


def test_admin_unfiltered_cypher_fragment_with_scope():
    fragment, params = admin_unfiltered_cypher_fragment(scope_filter="system")
    assert fragment == "(n.scope = $vis_scope)"
    assert params == {"vis_scope": "system"}
