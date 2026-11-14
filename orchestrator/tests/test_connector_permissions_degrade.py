# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""LUM-849 — the permissions LIST must be able to say it is incomplete.

``GET /api/v1/me/permissions`` has two branches. The healthy one asks the action
registry for every known connector and returns one row each (lazily defaulting
to ASK). The degrade branch — taken when ``actions.registry.list_actions``
raises — returns the caller's **explicit rows only**.

That second branch is the problem this module guards. A connector the caller has
never configured has no explicit row, so under degrade it **vanishes from the
list**. Before LUM-849 nothing on the wire said so: the page could not tell
"you have not set this" from "we could not read it", and a surface that
under-reports what may act on your behalf is worse than one that errors.

The fix is the ``Warning: 199`` advisory ``get_my_permission`` has always sent.

**§5a — the fake fails the way the real thing fails.** These tests break the
registry by making ``list_actions`` **raise**, which is what
``services.permission_connectors.known_action_connectors`` actually catches.
They do NOT monkeypatch ``known_action_connectors`` to return ``None`` directly: that
would test the branch while bypassing the mechanism that reaches it, and would
stay green if the ``except`` clause were narrowed to a type the real failure
does not raise.
"""

from __future__ import annotations

import logging

from tests.test_per_user_connector_permissions import _booted_client
from tests.test_per_user_connector_permissions import _create_user
from tests.test_per_user_connector_permissions import _hdr
from tests.test_per_user_connector_permissions import _stub_registry
from tests.test_per_user_connector_permissions import auth_env  # noqa: F401 — fixture
from tests.test_per_user_connector_permissions import perm_store  # noqa: F401 — fixture


def _break_registry(monkeypatch) -> None:
    """Make ``list_actions`` raise — the real failure ``known_action_connectors`` catches."""
    import actions.registry as _registry

    def boom():
        raise RuntimeError("registry down")

    monkeypatch.setattr(_registry, "list_actions", boom)


def test_list_when_action_registry_unavailable_then_attaches_warning(
    perm_store,  # noqa: F811
    auth_env,  # noqa: F811
    monkeypatch,
    caplog,
):
    """The degrade branch says so on the wire, not only in the log."""
    alice_id = _create_user(perm_store, user_id="alice", email="a@home.lan", role="user")
    _break_registry(monkeypatch)

    with caplog.at_level(logging.WARNING, logger="routes.connector_permissions"):
        with _booted_client() as client:
            r = client.get("/api/v1/me/permissions", headers=_hdr(alice_id))

    assert r.status_code == 200, r.text
    assert "Warning" in r.headers, (
        "the degrade branch returned no advisory — the list is incomplete and "
        "nothing on the wire says so"
    )
    assert "199" in r.headers["Warning"]


def test_list_when_registry_healthy_then_attaches_no_warning(
    perm_store,  # noqa: F811
    auth_env,  # noqa: F811
    monkeypatch,
):
    """The negative twin.

    Without this, a handler that sets the header unconditionally passes the
    test above — and the client would render "this list may be incomplete" on
    every healthy load, which is its own wrong-thing-said defect.
    """
    alice_id = _create_user(perm_store, user_id="alice", email="a@home.lan", role="user")
    _stub_registry(monkeypatch, ["imap", "smtp"])

    with _booted_client() as client:
        r = client.get("/api/v1/me/permissions", headers=_hdr(alice_id))

    assert r.status_code == 200, r.text
    assert "Warning" not in r.headers


def test_list_body_shape_unchanged_under_degrade(
    perm_store,  # noqa: F811
    auth_env,  # noqa: F811
    monkeypatch,
):
    """The header must not have been bought with an envelope.

    An alternative fix wrapped the rows in ``{"items": [...], "complete": bool}``.
    That breaks the shipped contract and every generated OpenAPI consumer, so
    this pins the body as a bare JSON array on BOTH branches.
    """
    alice_id = _create_user(perm_store, user_id="alice", email="a@home.lan", role="user")
    _stub_registry(monkeypatch, ["imap", "smtp"])

    # ONE booted client, with the registry taken down mid-flight. Two separate
    # `_booted_client()` blocks would be the obvious shape and is wrong twice
    # over: it re-runs startup (which in this container hits the Docker-only
    # `qdrant_client` import), and it models a restart rather than what actually
    # happens — a registry that fails while the app keeps serving.
    with _booted_client() as client:
        healthy = client.get("/api/v1/me/permissions", headers=_hdr(alice_id))
        assert isinstance(healthy.json(), list), healthy.text
        assert "Warning" not in healthy.headers

        _break_registry(monkeypatch)
        degraded = client.get("/api/v1/me/permissions", headers=_hdr(alice_id))

    assert degraded.status_code == 200, degraded.text
    assert isinstance(degraded.json(), list), degraded.text
    assert "199" in degraded.headers.get("Warning", "")


def test_healthy_list_returns_a_row_per_known_connector(
    perm_store,  # noqa: F811
    auth_env,  # noqa: F811
    monkeypatch,
):
    """The premise the web view's B1 arm depends on.

    The autonomy page renders "We couldn't load this setting right now" for a
    known group with no row. That copy is only correct if the HEALTHY branch
    always emits a row per known connector — otherwise it would be a lie shown
    to every user who has never touched a setting.

    Asserted here rather than assumed, because if it ever stops being true the
    web copy becomes wrong on the normal path and no web test would catch it.
    """
    alice_id = _create_user(perm_store, user_id="alice", email="a@home.lan", role="user")
    _stub_registry(monkeypatch, ["imap", "smtp"])

    with _booted_client() as client:
        r = client.get("/api/v1/me/permissions", headers=_hdr(alice_id))

    assert r.status_code == 200, r.text
    rows = r.json()
    assert {row["connector"] for row in rows} == {"imap", "smtp"}
    assert all(row["is_default"] is True for row in rows), rows
    assert all(row["mode"] == "ASK" for row in rows), rows


def test_delete_route_still_exists(
    perm_store,  # noqa: F811
    auth_env,  # noqa: F811
    monkeypatch,
):
    """LUM-849 removes the only caller of DELETE; the route must survive it.

    The web "Revert" button is gone — its information now lives in the
    "(default)" label instead. Nothing in the client calls this route any more,
    which makes it look like dead code to the next person pruning. It is not:
    it is the only way to clear an explicit row back to the lazy default, and
    the admin surface and API consumers still use it.
    """
    alice_id = _create_user(perm_store, user_id="alice", email="a@home.lan", role="user")
    _stub_registry(monkeypatch, ["imap", "smtp"])

    with _booted_client() as client:
        r = client.delete(
            "/api/v1/me/permissions/smtp",
            headers={**_hdr(alice_id), "Origin": "http://testserver"},
        )

    assert r.status_code == 200, r.text
    assert r.json()["is_default"] is True


def test_degrade_branch_projects_a_real_explicit_row_faithfully(
    perm_store,  # noqa: F811
    auth_env,  # noqa: F811
    monkeypatch,
):
    """The degrade branch's row-building code, actually exercised.

    **Found by code review, and the finding was about this file.** Every other
    test here creates a user with no explicit permission row, so
    ``get_user_permissions`` returns ``[]`` and the dict comprehension that
    builds the degrade payload (``connector_permissions.py:288-297``) runs over
    an empty list **every time**. Flipping ``is_default`` to ``True`` there, or
    deleting the ``scopes`` line, would have been caught by nothing — while the
    suite claimed to cover the branch.

    It also exposed a real defect: ``get_user_permissions`` did not select
    ``updated_at``, so every explicit row came back ``updated_at: null`` on this
    branch. A surface whose whole purpose is saying the true thing about what may
    act on your behalf, reporting "never changed" for a setting the user changed.
    Fixed in ``permissions.py``; pinned here.
    """
    alice_id = _create_user(perm_store, user_id="alice", email="a@home.lan", role="user")
    _stub_registry(monkeypatch, ["imap", "smtp"])

    with _booted_client() as client:
        put = client.put(
            "/api/v1/me/permissions/smtp",
            headers={**_hdr(alice_id), "Origin": "http://testserver"},
            json={"mode": "DO"},
        )
        assert put.status_code == 200, put.text

        _break_registry(monkeypatch)
        r = client.get("/api/v1/me/permissions", headers=_hdr(alice_id))

    assert r.status_code == 200, r.text
    rows = r.json()

    # Explicit rows only — `imap` was never set, so it is absent. That is the
    # whole reason the Warning header exists.
    assert [row["connector"] for row in rows] == ["smtp"], rows
    row = rows[0]
    assert row["mode"] == "DO"
    # An explicitly-set row is NOT the lazy default, and saying otherwise would
    # tell the user they had never chosen this.
    assert row["is_default"] is False
    assert row["scopes"] == []
    # The defect this test was written to catch.
    assert row["updated_at"] is not None, (
        "an explicitly-set permission reported no timestamp under degrade — "
        "get_user_permissions is not selecting updated_at"
    )
