# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Account-state changes cascade to every credential (LUM-885).

Delete, demote, every password path and "log out everywhere" revoke what the
user holds the way ``set_disabled`` always did: ``token_version``, auth
sessions, ``lmcp_`` MCP tokens, and (delete / disable / demote only) the invites
the user sent. All of it goes through one helper,
``services.users.revoke_credentials_in_transaction``.

These tests run on ``FakeUsersStore``, which does not model rollback; atomicity
is proven on a real Postgres in ``tests/integration/test_account_state_cascade_live.py``.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from datetime import timedelta
from datetime import timezone

import pytest
from fastapi.testclient import TestClient
from tests.test_auth_phase1 import FakeUsersStore
from tests.test_password_reset_service import _ResetFakeStore

import config
from services import mcp_tokens as _mcp_tokens
from services import password_reset
from services import users as users_svc

pytest_plugins = ("tests.test_auth_phase1",)

PW = "verylongpassword12"


# ---------------------------------------------------------------------------
# Fixtures and seed helpers
# ---------------------------------------------------------------------------


def _seed_session(store: FakeUsersStore, user_id: str) -> str:
    sid = uuid.uuid4().hex
    store.auth_sessions[sid] = {
        "id": sid,
        "user_id": user_id,
        "family_id": uuid.uuid4().hex,
        "refresh_token_hash": "h" + sid,
        "created_at": datetime.now(timezone.utc),
        "last_used_at": None,
        "expires_at": datetime.now(timezone.utc) + timedelta(days=1),
        "revoked_at": None,
        "device_label": None,
        "ip_hash": None,
        "ua_hash": None,
    }
    return sid


def _seed_token(store: FakeUsersStore, user_id: str) -> str:
    tid = uuid.uuid4().hex
    store.mcp_tokens[tid] = {
        "id": tid,
        "user_id": user_id,
        "token_prefix": tid[:16],
        "token_hash": "h" + tid,
        "label": "t",
        "scopes": None,
        "created_at": datetime.now(timezone.utc),
        "last_used_at": None,
        "expires_at": None,
        "revoked_at": None,
    }
    return tid


def _seed_invite(store: FakeUsersStore, created_by: str, *, used: bool = False) -> str:
    iid = uuid.uuid4().hex
    store.invites[iid] = {
        "id": iid,
        "created_by": created_by,
        "used_at": datetime.now(timezone.utc) if used else None,
        "revoked_at": None,
    }
    return iid


class World:
    """Admin A, second admin B and member M, each with a session and an MCP token."""

    def __init__(self, store: FakeUsersStore) -> None:
        self.store = store
        self.A = users_svc.create_user("a@home.lan", PW, "admin").id
        self.B = users_svc.create_user("b@home.lan", PW, "admin").id
        self.M = users_svc.create_user("m@home.lan", PW, "user").id
        self.tok: dict[str, str] = {}
        self.sid: dict[str, str] = {}
        for uid in (self.A, self.B, self.M):
            self.tok[uid] = _seed_token(store, uid)
            self.sid[uid] = _seed_session(store, uid)

    def tv(self, uid: str) -> int:
        return int(self.store.rows[uid]["token_version"])

    def token_revoked(self, uid: str) -> bool:
        return self.store.mcp_tokens[self.tok[uid]]["revoked_at"] is not None

    def session_revoked(self, uid: str) -> bool:
        return self.store.auth_sessions[self.sid[uid]]["revoked_at"] is not None

    def assert_untouched(self, *uids: str) -> None:
        """``test_cascade_does_not_touch_other_users``: nothing moved for ``uids``."""
        for uid in uids:
            assert not self.token_revoked(uid), uid
            assert not self.session_revoked(uid), uid
            assert self.tv(uid) == self._tv0[uid], uid

    def snapshot(self) -> None:
        self._tv0 = {u: self.tv(u) for u in (self.A, self.B, self.M)}


@pytest.fixture
def audits(monkeypatch):
    """Record (action, actor, input_summary) from the three audit writers."""
    from services import auth_sessions as _sess
    from services import user_invites as _inv

    rec: list[tuple[str, str, dict]] = []

    def _make(real_name):
        def _rec(action, *, user_id, input_summary=None, result_summary=None):
            rec.append((action, user_id, input_summary or {}))

        return _rec

    monkeypatch.setattr(_mcp_tokens, "_emit_audit", _make("mcp"))
    monkeypatch.setattr(_sess, "_emit_audit", _make("sess"))
    monkeypatch.setattr(_inv, "_emit_audit", _make("inv"))
    return rec


@pytest.fixture
def caches(monkeypatch):
    """Record cache invalidations that ``finish_credential_revocation`` makes."""
    import auth
    import permissions

    calls: dict[str, list] = {"tv": [], "sid": [], "perm": []}
    monkeypatch.setattr(auth, "invalidate_token_version_cache", lambda u: calls["tv"].append(u))
    monkeypatch.setattr(auth, "invalidate_sid_cache", lambda s: calls["sid"].append(s))
    monkeypatch.setattr(permissions, "clear_cache_for_user", lambda u: calls["perm"].append(u))
    return calls


@pytest.fixture
def world(users_store, auth_env, audits, caches) -> World:
    w = World(users_store)
    w.snapshot()
    return w


def _actions(audits, action):
    return [a for a in audits if a[0] == action]


MCP_AUDIT = "__mcp_token__.cascade_revoked"


# ---------------------------------------------------------------------------
# 1. set_disabled still revokes everything, through the helper
# ---------------------------------------------------------------------------


def test_set_disabled_still_revokes_everything(world, users_store, audits, caches):
    users_store.stmt_log.clear()
    users_svc.set_disabled(world.M, True, by_admin_user_id=world.A)

    # D9: the tv bump takes the users row lock first.
    assert users_store.stmt_log[0].startswith("update users set token_version = token_version + 1")
    assert world.token_revoked(world.M)
    assert world.session_revoked(world.M)
    assert world.tv(world.M) == world._tv0[world.M] + 1
    mcp = _actions(audits, MCP_AUDIT)
    assert len(mcp) == 1
    assert mcp[0][1] == world.A
    assert mcp[0][2]["by_admin_user_id"] == world.A
    assert caches["perm"] == [world.M]
    world.assert_untouched(world.A, world.B)


# ---------------------------------------------------------------------------
# 2-3. delete
# ---------------------------------------------------------------------------


def test_delete_user_revokes_and_retains_mcp_tokens(world, users_store, audits):
    assert users_svc.delete_user(world.M, by_admin_user_id=world.A) is True

    assert world.M not in users_store.rows
    # ADR 017: the forensic row is kept, revoked.
    assert world.tok[world.M] in users_store.mcp_tokens
    assert world.token_revoked(world.M)
    mcp = _actions(audits, MCP_AUDIT)
    assert [a[1] for a in mcp] == [world.A]
    assert not [s for s in users_store.auth_sessions.values() if s["user_id"] == world.M]
    world.assert_untouched(world.A, world.B)


def test_delete_route_threads_admin_id(world, users_store, audits):
    import main
    from tests.test_auth_phase2 import _mint

    with TestClient(main.app) as client:
        r = client.delete(
            f"/api/v1/admin/users/{world.M}",
            headers={"Authorization": f"Bearer {_mint(world.A, 'admin')}"},
        )
    assert r.status_code == 200
    assert r.json() == {"ok": True}
    assert [a[1] for a in _actions(audits, MCP_AUDIT)] == [world.A]


# ---------------------------------------------------------------------------
# 4-6. role changes
# ---------------------------------------------------------------------------


def test_demotion_full_cascade(world, users_store):
    users_svc.update_role(world.B, "user", by_admin_user_id=world.A)

    assert users_store.rows[world.B]["role"] == "user"
    assert world.token_revoked(world.B)
    assert world.session_revoked(world.B)
    assert world.tv(world.B) == world._tv0[world.B] + 1
    world.assert_untouched(world.A, world.M)


def test_promotion_bumps_tv_only(world, users_store, audits):
    users_svc.update_role(world.M, "admin", by_admin_user_id=world.A)

    assert users_store.rows[world.M]["role"] == "admin"
    assert world.tv(world.M) == world._tv0[world.M] + 1
    assert not world.token_revoked(world.M)
    assert not world.session_revoked(world.M)
    assert _actions(audits, MCP_AUDIT) == []
    world.assert_untouched(world.A, world.B)


def test_same_role_patch_is_noop(world, users_store):
    users_store.exec_log.clear()
    users_svc.update_role(world.M, "user", by_admin_user_id=world.A)

    assert world.tv(world.M) == world._tv0[world.M]
    assert not world.token_revoked(world.M)
    assert not [q for q, _ in users_store.exec_log if "update users set role" in q.lower()]


def test_update_role_on_missing_user_returns_none(world):
    assert users_svc.update_role("nope", "admin") is None


# ---------------------------------------------------------------------------
# 7. the ID-001 attack: a demoted admin's old JWT cannot re-promote them
# ---------------------------------------------------------------------------


def _login(client, email):
    r = client.post("/api/v1/auth/login", json={"email": email, "password": PW})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def test_demoted_admin_old_jwt_cannot_repromote(users_store, auth_env):
    import main

    a = users_svc.create_user("a@home.lan", PW, "admin").id
    b = users_svc.create_user("b@home.lan", PW, "admin").id
    with TestClient(main.app) as client_a, TestClient(main.app) as client_b:
        t_a = _login(client_a, "a@home.lan")
        t_old = _login(client_b, "b@home.lan")

        # Negative control: the old token is good before the demotion, so a later
        # 401 cannot come from a malformed or typ-less token.
        ok = client_b.get("/api/v1/admin/users", headers={"Authorization": f"Bearer {t_old}"})
        assert ok.status_code == 200

        r = client_a.patch(
            f"/api/v1/admin/users/{b}",
            json={"role": "user"},
            headers={"Authorization": f"Bearer {t_a}"},
        )
        assert r.status_code == 200, r.text

        repromote = client_b.patch(
            f"/api/v1/admin/users/{b}",
            json={"role": "admin"},
            headers={"Authorization": f"Bearer {t_old}"},
        )
        assert repromote.status_code == 401
        refreshed = client_b.post("/api/v1/auth/refresh")
        assert refreshed.status_code == 401
    assert users_store.rows[b]["role"] == "user"
    assert users_store.rows[a]["role"] == "admin"


# ---------------------------------------------------------------------------
# 8. password change, all four entry points
# ---------------------------------------------------------------------------


def _password_paths(world):
    def self_change():
        users_svc.change_own_password(world.M, PW, "brandnewpassword12")
        return world.M

    def admin_reset():
        users_svc.admin_reset_user_password(world.M, "brandnewpassword12", by_admin_user_id=world.A)
        return world.A

    def cli():
        users_svc.cli_reset_password(
            email="m@home.lan", user_id=None, new_password="brandnewpassword12"
        )
        return world.M

    return {"self": self_change, "admin": admin_reset, "cli": cli}


@pytest.mark.parametrize("path", ["self", "admin", "cli"])
def test_password_change_revokes_mcp_tokens(world, audits, path):
    expected_actor = _password_paths(world)[path]()

    assert world.token_revoked(world.M)
    assert world.session_revoked(world.M)
    # Exactly +1, not +2: the password UPDATE must not bump on top of the helper.
    assert world.tv(world.M) == world._tv0[world.M] + 1
    assert [a[1] for a in _actions(audits, MCP_AUDIT)] == [expected_actor]
    world.assert_untouched(world.A, world.B)


def test_password_change_via_email_reset_redeem_revokes_mcp_tokens(
    monkeypatch, auth_env, audits, caches
):
    store = _ResetFakeStore()
    monkeypatch.setattr(config, "get_metadata_store", lambda: store)
    monkeypatch.setattr("services.users.config.get_metadata_store", lambda: store, raising=False)
    config._instances["metadata_store"] = store
    m = users_svc.create_user("m@home.lan", PW, "user").id
    other = users_svc.create_user("o@home.lan", PW, "user").id
    tok = _seed_token(store, m)
    other_tok = _seed_token(store, other)
    _seed_session(store, m)
    tv0 = store.rows[m]["token_version"]

    minted = password_reset.mint(store, m)
    password_reset.redeem(minted.plaintext, "brandnewpassword12")

    assert store.mcp_tokens[tok]["revoked_at"] is not None
    assert store.mcp_tokens[other_tok]["revoked_at"] is None
    assert store.rows[m]["token_version"] == tv0 + 1
    assert [a[1] for a in _actions(audits, MCP_AUDIT)] == [m]


# ---------------------------------------------------------------------------
# 9. logout-all
# ---------------------------------------------------------------------------


def test_logout_all_revokes_mcp_tokens(world, caches):
    import main
    from tests.test_auth_phase2 import _mint

    with TestClient(main.app) as client:
        r = client.post(
            "/api/v1/me/logout-all",
            headers={"Authorization": f"Bearer {_mint(world.M, 'user')}"},
        )
    assert r.status_code == 204
    assert world.token_revoked(world.M)
    assert world.session_revoked(world.M)
    assert world.tv(world.M) == world._tv0[world.M] + 1
    assert world.M in caches["tv"]
    world.assert_untouched(world.A, world.B)


def test_logout_all_cross_origin_cookie_request_is_refused(world, monkeypatch):
    """The CSRF guard still fronts the route after it moved onto the helper:
    a cookie-only request from a foreign Origin never reaches the revocation."""
    import main

    monkeypatch.setenv("LUMOGIS_PUBLIC_ORIGIN", "https://lumogis.example")
    with TestClient(main.app) as client:
        client.cookies.set("lumogis_refresh", "x")
        r = client.post("/api/v1/me/logout-all", headers={"Origin": "https://evil.example"})
    assert r.status_code in (401, 403)
    assert not world.token_revoked(world.M)
    assert world.tv(world.M) == world._tv0[world.M]


# ---------------------------------------------------------------------------
# 10-11. the helper itself
# ---------------------------------------------------------------------------


def test_revoke_credentials_flags(world, users_store):
    ms = config.get_metadata_store()
    users_svc.revoke_credentials_in_transaction(
        ms, world.M, revoke_mcp=False, revoke_sessions=False
    )
    assert world.tv(world.M) == world._tv0[world.M] + 1
    assert not world.token_revoked(world.M)
    assert not world.session_revoked(world.M)

    users_svc.revoke_credentials_in_transaction(ms, world.M, revoke_mcp=False)
    assert world.session_revoked(world.M)
    assert not world.token_revoked(world.M)


def test_finish_runs_caches_before_audits(world, monkeypatch, caches, audits, caplog):
    ms = config.get_metadata_store()
    rev = users_svc.revoke_credentials_in_transaction(ms, world.M)

    def boom(*a, **k):
        raise RuntimeError("audit store down")

    monkeypatch.setattr(_mcp_tokens, "_emit_audit", boom)
    users_svc.finish_credential_revocation(rev)  # must not raise

    assert caches["tv"] == [world.M]
    assert caches["sid"] == [world.sid[world.M]]
    assert "mcp cascade audit failed" in caplog.text


def test_finish_survives_cache_failure(world, monkeypatch, audits, caplog):
    import auth

    ms = config.get_metadata_store()
    rev = users_svc.revoke_credentials_in_transaction(ms, world.M)
    cleared: list[str] = []

    def boom(_):
        raise RuntimeError("cache down")

    import permissions

    monkeypatch.setattr(auth, "invalidate_token_version_cache", boom)
    monkeypatch.setattr(permissions, "clear_cache_for_user", lambda u: cleared.append(u))
    users_svc.finish_credential_revocation(rev, clear_permission_cache=True)  # must not raise

    assert cleared == [world.M]
    assert len(_actions(audits, MCP_AUDIT)) == 1
    assert "token_version cache invalidation failed" in caplog.text


# ---------------------------------------------------------------------------
# 11b-11d. invites and concurrent delete
# ---------------------------------------------------------------------------


def _invite_world(world):
    b_open = _seed_invite(world.store, world.B)
    b_used = _seed_invite(world.store, world.B, used=True)
    a_open = _seed_invite(world.store, world.A)
    return b_open, b_used, a_open


@pytest.mark.parametrize("flow", ["delete", "disable", "demote"])
def test_invites_revoked_on_delete_disable_demote(world, audits, flow):
    b_open, b_used, a_open = _invite_world(world)
    invites = world.store.invites

    if flow == "delete":
        users_svc.delete_user(world.B, by_admin_user_id=world.A)
    elif flow == "disable":
        users_svc.set_disabled(world.B, True, by_admin_user_id=world.A)
    else:
        users_svc.update_role(world.B, "user", by_admin_user_id=world.A)

    assert invites[b_open]["revoked_at"] is not None
    assert invites[b_used]["revoked_at"] is None
    assert invites[a_open]["revoked_at"] is None
    revoked = _actions(audits, "__user_invite__.revoked")
    assert len(revoked) == 1
    assert revoked[0][1] == world.A
    assert revoked[0][2]["cascade"] is True
    assert revoked[0][2]["invite_id"] == b_open


def test_invites_kept_on_password_change_logout_all_promotion(world):
    b_open, _b_used, a_open = _invite_world(world)
    m_open = _seed_invite(world.store, world.M)

    users_svc.change_own_password(world.B, PW, "brandnewpassword12")
    users_svc.revoke_all_credentials(world.B)
    users_svc.update_role(world.M, "admin")

    for iid in (b_open, a_open, m_open):
        assert world.store.invites[iid]["revoked_at"] is None


def test_delete_user_concurrent_delete_returns_false(world, users_store, monkeypatch):
    """The row lock finds no row (a concurrent delete won): no 500, no revoke."""
    real = users_store.fetch_one

    def lock_finds_nothing(query, params=None):
        if (
            " ".join(query.split())
            .lower()
            .startswith("select id from users where id = %s for update")
        ):
            return None
        return real(query, params)

    monkeypatch.setattr(users_store, "fetch_one", lock_finds_nothing)
    users_store.exec_log.clear()

    assert users_svc.delete_user(world.M, by_admin_user_id=world.A) is False
    assert not [q for q, _ in users_store.exec_log if q.lower().startswith("delete from users")]
    assert not world.token_revoked(world.M)
    assert world.tv(world.M) == world._tv0[world.M]


def test_delete_route_returns_404_when_target_vanishes_between_check_and_call(world, monkeypatch):
    import main
    from tests.test_auth_phase2 import _mint

    monkeypatch.setattr(users_svc, "delete_user", lambda *a, **k: False)
    with TestClient(main.app) as client:
        r = client.delete(
            f"/api/v1/admin/users/{world.M}",
            headers={"Authorization": f"Bearer {_mint(world.A, 'admin')}"},
        )
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Edge: demote and disable in one PATCH
# ---------------------------------------------------------------------------


def test_demote_and_disable_in_one_patch_runs_both_cascades(world, users_store):
    import main
    from tests.test_auth_phase2 import _mint

    with TestClient(main.app) as client:
        r = client.patch(
            f"/api/v1/admin/users/{world.B}",
            json={"role": "user", "disabled": True},
            headers={"Authorization": f"Bearer {_mint(world.A, 'admin')}"},
        )
    assert r.status_code == 200, r.text
    assert users_store.rows[world.B]["role"] == "user"
    assert users_store.rows[world.B]["disabled"] is True
    assert world.token_revoked(world.B)
    assert world.session_revoked(world.B)
    assert world.tv(world.B) == world._tv0[world.B] + 2
    world.assert_untouched(world.A, world.M)


def test_self_demotion_by_non_last_admin_signs_caller_out(world, users_store):
    import main
    from tests.test_auth_phase2 import _mint

    with TestClient(main.app) as client:
        r = client.patch(
            f"/api/v1/admin/users/{world.A}",
            json={"role": "user"},
            headers={"Authorization": f"Bearer {_mint(world.A, 'admin')}"},
        )
    assert r.status_code == 200, r.text
    assert world.token_revoked(world.A)
    assert world.session_revoked(world.A)
    world.assert_untouched(world.B, world.M)


def test_patch_role_on_missing_user_404(world):
    import main
    from tests.test_auth_phase2 import _mint

    with TestClient(main.app) as client:
        r = client.patch(
            "/api/v1/admin/users/does-not-exist",
            json={"role": "admin"},
            headers={"Authorization": f"Bearer {_mint(world.A, 'admin')}"},
        )
    assert r.status_code == 404
