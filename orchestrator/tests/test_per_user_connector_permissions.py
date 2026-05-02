# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Unit + route tests for per-user connector permissions.

Pinned by the test matrix in plan ``per_user_connector_permissions``
§"Test cases / Unit tests" and §"Route tests" (tests 1-34). Each test
docstring carries the plan-test-number for traceability.

Fixture strategy
----------------
The shared :func:`tests.conftest._override_config` autouse installs a
mock metadata store that returns ``None`` / ``[]`` for every query —
useless for assertions of the form "insert row → read it back". Tests
in this module install :class:`_FakeConnectorPermStore` via the
``perm_store`` fixture, which models the SQL surface used by
``orchestrator/permissions.py`` plus the ``users`` table accesses
needed by the route layer (admin existence checks, email lookup).

Cache hygiene
-------------
``permissions._mode_cache`` is a module-level dict; the ``perm_store``
fixture clears it before every test so cache state never leaks across
the suite.

Route auth
----------
Route tests mint JWTs inline via ``jwt.encode`` (mirroring
``tests/integration/test_two_user_isolation.py::_hdr``) and toggle the
``AUTH_ENABLED=true`` environment via ``monkeypatch.setenv`` per test.
The route TestClient is constructed inside a ``with``-block so the
FastAPI lifespan runs (matches ``_booted_client()`` from the same
file).
"""

from __future__ import annotations

import contextlib
import logging
import os
from datetime import datetime
from datetime import timezone
from typing import Any

import pytest
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# In-memory fake store covering the connector_permissions and
# routine_do_tracking SQL plus the users-table reads needed by the
# admin route layer (existence check + email lookup).
# ---------------------------------------------------------------------------


class _FakeConnectorPermStore:
    """Honours every SQL statement issued by ``permissions.py`` plus the
    minimal ``users`` SQL the route layer issues for admin gates.

    Recognised statements (normalised: lowercased, whitespace-collapsed):

    Mutating
        * INSERT INTO connector_permissions (user_id, connector, mode) VALUES (...)
          ON CONFLICT (user_id, connector) DO UPDATE SET mode = EXCLUDED.mode, updated_at = NOW()
        * DELETE FROM connector_permissions WHERE user_id = %s AND connector = %s
        * INSERT INTO routine_do_tracking (user_id, connector, action_type, approval_count) VALUES (...)
          ON CONFLICT (user_id, connector, action_type) DO UPDATE
          SET approval_count = routine_do_tracking.approval_count + 1, updated_at = NOW()
        * INSERT INTO routine_do_tracking (user_id, connector, action_type, auto_approved, granted_at) VALUES (...)
          ON CONFLICT (user_id, connector, action_type) DO UPDATE
          SET auto_approved = TRUE, granted_at = NOW(), updated_at = NOW()
        * INSERT INTO users ...
        * INSERT INTO action_log ...

    Reading
        * SELECT mode FROM connector_permissions WHERE user_id = %s AND connector = %s
        * SELECT connector, mode FROM connector_permissions WHERE user_id = %s ORDER BY connector
        * SELECT user_id, connector, mode FROM connector_permissions ORDER BY user_id, connector
        * SELECT approval_count, edit_count, auto_approved FROM routine_do_tracking WHERE ...
        * SELECT * FROM users WHERE id = %s   (services.users.get_user_by_id)

    Anything else: append to ``exec_log`` and return None / [] so route
    tests can introspect what SQL the orchestrator issued without the
    fake claiming to model surfaces it doesn't.
    """

    def __init__(self) -> None:
        # connector_permissions: keyed by (user_id, connector)
        self.cp: dict[tuple[str, str], dict[str, Any]] = {}
        # routine_do_tracking: keyed by (user_id, connector, action_type)
        self.rdt: dict[tuple[str, str, str], dict[str, Any]] = {}
        # users: minimal shape for get_user_by_id (admin existence check
        # and email lookup on the new admin routes).
        self.users: dict[str, dict[str, Any]] = {}
        self.action_log: list[tuple[str, tuple]] = []
        self.exec_log: list[tuple[str, tuple]] = []
        self._next_cp_id = 1

    def ping(self) -> bool:
        return True

    def close(self) -> None:
        pass

    @contextlib.contextmanager
    def transaction(self):
        yield

    @staticmethod
    def _norm(query: str) -> str:
        return " ".join(query.split()).lower()

    # ----- write path --------------------------------------------------

    def execute(self, query: str, params: tuple | None = None) -> None:
        self.exec_log.append((query, params or ()))
        q = self._norm(query)
        p = params or ()

        if q.startswith("insert into connector_permissions"):
            uid, connector, mode = p
            now = datetime.now(timezone.utc)
            existing = self.cp.get((uid, connector))
            if existing is None:
                self.cp[(uid, connector)] = {
                    "id": self._next_cp_id,
                    "user_id": uid,
                    "connector": connector,
                    "mode": mode,
                    "created_at": now,
                    "updated_at": now,
                }
                self._next_cp_id += 1
            else:
                existing["mode"] = mode
                existing["updated_at"] = now
            return

        if q.startswith("delete from connector_permissions"):
            uid, connector = p
            self.cp.pop((uid, connector), None)
            return

        if "insert into routine_do_tracking" in q and "auto_approved, granted_at" in q:
            uid, connector, action_type = p
            now = datetime.now(timezone.utc)
            existing = self.rdt.get((uid, connector, action_type))
            if existing is None:
                self.rdt[(uid, connector, action_type)] = {
                    "user_id": uid,
                    "connector": connector,
                    "action_type": action_type,
                    "approval_count": 0,
                    "edit_count": 0,
                    "auto_approved": True,
                    "granted_at": now,
                    "updated_at": now,
                }
            else:
                existing["auto_approved"] = True
                existing["granted_at"] = now
                existing["updated_at"] = now
            return

        if q.startswith("insert into routine_do_tracking"):
            uid, connector, action_type = p
            now = datetime.now(timezone.utc)
            existing = self.rdt.get((uid, connector, action_type))
            if existing is None:
                self.rdt[(uid, connector, action_type)] = {
                    "user_id": uid,
                    "connector": connector,
                    "action_type": action_type,
                    "approval_count": 1,
                    "edit_count": 0,
                    "auto_approved": False,
                    "granted_at": None,
                    "updated_at": now,
                }
            else:
                existing["approval_count"] += 1
                existing["updated_at"] = now
            return

        if q.startswith("insert into users"):
            uid, email, password_hash, role = p
            self.users[uid] = {
                "id": uid,
                "email": email,
                "password_hash": password_hash,
                "role": role,
                "disabled": False,
                "created_at": datetime.now(timezone.utc),
                "last_login_at": None,
                "refresh_token_jti": None,
            }
            return

        if q.startswith("update users set last_login_at = now()"):
            uid = p[0]
            if uid in self.users:
                self.users[uid]["last_login_at"] = datetime.now(timezone.utc)
            return

        if q.startswith("update users set refresh_token_jti ="):
            jti, uid = p
            if uid in self.users:
                self.users[uid]["refresh_token_jti"] = jti
            return

        if q.startswith("update users set disabled = true"):
            uid = p[0]
            if uid in self.users:
                self.users[uid]["disabled"] = True
                self.users[uid]["refresh_token_jti"] = None
            return

        if q.startswith("update users set disabled = false"):
            uid = p[0]
            if uid in self.users:
                self.users[uid]["disabled"] = False
            return

        if q.startswith("delete from users where id ="):
            self.users.pop(p[0], None)
            return

        if q.startswith("insert into action_log"):
            self.action_log.append((query, p))
            return

    # ----- read path ---------------------------------------------------

    def fetch_one(self, query: str, params: tuple | None = None) -> dict | None:
        q = self._norm(query)
        p = params or ()

        if q.startswith("select mode from connector_permissions"):
            uid, connector = p
            row = self.cp.get((uid, connector))
            return {"mode": row["mode"]} if row is not None else None

        if q.startswith(
            "select approval_count, edit_count, auto_approved from routine_do_tracking"
        ):
            uid, connector, action_type = p
            row = self.rdt.get((uid, connector, action_type))
            if row is None:
                return None
            return {
                "approval_count": row["approval_count"],
                "edit_count": row["edit_count"],
                "auto_approved": row["auto_approved"],
            }

        if q.startswith("select * from users where id ="):
            uid = p[0]
            row = self.users.get(uid)
            return dict(row) if row else None

        if q.startswith("select * from users where lower(email) ="):
            target = p[0].lower()
            for row in self.users.values():
                if row["email"].lower() == target:
                    return dict(row)
            return None

        if q.startswith("select id from users where lower(email) ="):
            target = p[0].lower()
            for row in self.users.values():
                if row["email"].lower() == target:
                    return {"id": row["id"]}
            return None

        if q.startswith("select count(*) as n from users where role = 'admin'"):
            n = sum(1 for r in self.users.values() if r["role"] == "admin" and not r["disabled"])
            return {"n": n}

        if q.startswith("select count(*) as n from users"):
            return {"n": len(self.users)}

        if q.startswith("select id from users where role = 'admin'"):
            admins = sorted(
                (r for r in self.users.values() if r["role"] == "admin" and not r["disabled"]),
                key=lambda r: r["created_at"],
            )
            return {"id": admins[0]["id"]} if admins else None

        if q.startswith("select refresh_token_jti from users where id ="):
            row = self.users.get(p[0])
            return {"refresh_token_jti": row["refresh_token_jti"]} if row else None

        if q.startswith("insert into audit_log"):
            return {"id": 1}

        return None

    def fetch_all(self, query: str, params: tuple | None = None) -> list[dict]:
        q = self._norm(query)
        p = params or ()

        if q.startswith("select connector, mode from connector_permissions where user_id"):
            (uid,) = p
            rows = [
                {
                    "connector": r["connector"],
                    "mode": r["mode"],
                    "updated_at": r["updated_at"],
                }
                for (u, _c), r in self.cp.items()
                if u == uid
            ]
            return sorted(rows, key=lambda r: r["connector"])

        if q.startswith("select user_id, connector, mode from connector_permissions"):
            rows = [
                {
                    "user_id": r["user_id"],
                    "connector": r["connector"],
                    "mode": r["mode"],
                    "updated_at": r["updated_at"],
                }
                for r in self.cp.values()
            ]
            return sorted(rows, key=lambda r: (r["user_id"], r["connector"]))

        if q.startswith("select * from users order by created_at"):
            return sorted(
                (dict(r) for r in self.users.values()),
                key=lambda r: r["created_at"],
            )

        if q.startswith("update mcp_tokens set revoked_at = now() where user_id = %s"):
            return []

        return []


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def perm_store(monkeypatch):
    """Install a fake metadata store and reset the permissions cache.

    Overrides the autouse ``mock_metadata_store`` slot from
    ``tests/conftest.py``. The fake captures every issued SQL statement
    so route tests can both read back the row state and assert which
    queries were issued.
    """
    import permissions as _permissions

    import config as _config

    store = _FakeConnectorPermStore()
    _config._instances["metadata_store"] = store
    monkeypatch.setattr(_permissions, "_mode_cache", {})
    yield store
    _config._instances.pop("metadata_store", None)


@pytest.fixture
def auth_env(monkeypatch):
    """Family-LAN AUTH_ENABLED=true for route tests."""
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTH_SECRET", "test-access-secret")
    monkeypatch.setenv("LUMOGIS_JWT_REFRESH_SECRET", "test-refresh-secret")
    monkeypatch.setenv("ACCESS_TOKEN_TTL_SECONDS", "900")
    monkeypatch.setenv("REFRESH_TOKEN_TTL_SECONDS", "3600")
    monkeypatch.setenv("LUMOGIS_REFRESH_COOKIE_SECURE", "false")
    yield


@contextlib.contextmanager
def _booted_client():
    import main

    with TestClient(main.app) as client:
        yield client


def _hdr(user_id: str, role: str = "user") -> dict[str, str]:
    import jwt

    token = jwt.encode(
        {
            "sub": user_id,
            "role": role,
            "iat": int(datetime.now(timezone.utc).timestamp()),
            "exp": int(datetime.now(timezone.utc).timestamp()) + 600,
        },
        os.environ["AUTH_SECRET"],
        algorithm="HS256",
    )
    return {"Authorization": f"Bearer {token}"}


def _create_user(
    perm_store: _FakeConnectorPermStore, *, user_id: str, email: str, role: str
) -> str:
    perm_store.users[user_id] = {
        "id": user_id,
        "email": email,
        "password_hash": "x",
        "role": role,
        "disabled": False,
        "created_at": datetime.now(timezone.utc),
        "last_login_at": None,
        "refresh_token_jti": None,
    }
    return user_id


def _seed_perm(perm_store, *, user_id: str, connector: str, mode: str) -> None:
    now = datetime.now(timezone.utc)
    perm_store.cp[(user_id, connector)] = {
        "id": perm_store._next_cp_id,
        "user_id": user_id,
        "connector": connector,
        "mode": mode,
        "created_at": now,
        "updated_at": now,
    }
    perm_store._next_cp_id += 1


def _stub_registry(monkeypatch, connectors: list[str]) -> None:
    """Force ``actions.registry.list_actions`` to return a fixed connector set.

    Tests that need ``_known_connectors()`` to be deterministic call
    this so the assertion is genuinely about per-user state, not about
    whatever connectors happen to be auto-registered.
    """
    fake_specs = [
        {
            "name": f"act-{c}",
            "connector": c,
            "action_type": "noop",
            "is_write": False,
            "is_reversible": False,
            "reverse_action_name": None,
            "definition": {},
        }
        for c in connectors
    ]
    import actions.registry as _registry

    monkeypatch.setattr(_registry, "list_actions", lambda: fake_specs)


# ---------------------------------------------------------------------------
# Unit tests — direct service-layer assertions.
# ---------------------------------------------------------------------------


def test_get_connector_mode_returns_per_user_row_when_present(perm_store):
    """Plan test 1."""
    from permissions import get_connector_mode

    _seed_perm(perm_store, user_id="alice", connector="filesystem-mcp", mode="DO")
    assert get_connector_mode(user_id="alice", connector="filesystem-mcp") == "DO"


def test_get_connector_mode_falls_through_to_default_mode_constant_on_miss(perm_store):
    """Plan test 2."""
    from permissions import get_connector_mode

    assert get_connector_mode(user_id="alice", connector="filesystem-mcp") == "ASK"


def test_get_connector_mode_caches_per_user_per_connector_only(perm_store):
    """Plan test 3."""
    from permissions import get_connector_mode
    from permissions import invalidate_cache

    _seed_perm(perm_store, user_id="alice", connector="filesystem-mcp", mode="DO")
    assert get_connector_mode(user_id="alice", connector="filesystem-mcp") == "DO"
    perm_store.cp[("alice", "filesystem-mcp")]["mode"] = "ASK"
    assert get_connector_mode(user_id="alice", connector="filesystem-mcp") == "DO"
    invalidate_cache("alice", "filesystem-mcp")
    assert get_connector_mode(user_id="alice", connector="filesystem-mcp") == "ASK"


def test_get_connector_mode_cache_does_not_leak_between_users(perm_store):
    """This proves the per-user cache slot is sticky on miss (caches the
    lazy fallback) — NOT that the DB is wrong. The second 'ASK' assertion
    is the cache returning a STALE value because bob's row was inserted
    behind the cache's back via direct SQL; the third 'DO' assertion is
    correct DB state surfacing after explicit invalidate. If you remove
    the second assertion thinking it's a bug, you've removed the
    regression test for cache-per-user isolation.

    Plan test 4 (verbatim docstring per ARBITRATE-R2 D6.2).
    """
    from permissions import get_connector_mode
    from permissions import invalidate_cache

    _seed_perm(perm_store, user_id="alice", connector="filesystem-mcp", mode="DO")
    assert get_connector_mode(user_id="bob", connector="filesystem-mcp") == "ASK"
    _seed_perm(perm_store, user_id="bob", connector="filesystem-mcp", mode="DO")
    assert get_connector_mode(user_id="bob", connector="filesystem-mcp") == "ASK"
    invalidate_cache("bob", "filesystem-mcp")
    assert get_connector_mode(user_id="bob", connector="filesystem-mcp") == "DO"


def test_set_connector_mode_upserts_and_invalidates_one_cache_slot(perm_store):
    """Plan test 5."""
    from permissions import _mode_cache
    from permissions import get_connector_mode
    from permissions import set_connector_mode

    _seed_perm(perm_store, user_id="alice", connector="filesystem-mcp", mode="ASK")
    _seed_perm(perm_store, user_id="bob", connector="filesystem-mcp", mode="DO")
    get_connector_mode(user_id="alice", connector="filesystem-mcp")
    get_connector_mode(user_id="bob", connector="filesystem-mcp")
    assert ("alice", "filesystem-mcp") in _mode_cache
    assert ("bob", "filesystem-mcp") in _mode_cache
    set_connector_mode(user_id="alice", connector="filesystem-mcp", mode="DO")
    assert ("alice", "filesystem-mcp") not in _mode_cache
    assert ("bob", "filesystem-mcp") in _mode_cache


def test_set_connector_mode_rejects_invalid_mode(perm_store):
    """Plan test 6."""
    from permissions import set_connector_mode

    with pytest.raises(ValueError):
        set_connector_mode(user_id="alice", connector="filesystem-mcp", mode="MAYBE")


def test_set_connector_mode_rejects_empty_user_id(perm_store):
    """Plan test 7."""
    from permissions import set_connector_mode

    with pytest.raises(TypeError):
        set_connector_mode(user_id="", connector="filesystem-mcp", mode="ASK")


def test_clear_cache_for_user_evicts_all_users_keys_only(perm_store):
    """Plan test 8."""
    from permissions import _mode_cache
    from permissions import clear_cache_for_user
    from permissions import get_connector_mode

    _seed_perm(perm_store, user_id="alice", connector="filesystem-mcp", mode="DO")
    _seed_perm(perm_store, user_id="alice", connector="email-mcp", mode="DO")
    _seed_perm(perm_store, user_id="bob", connector="filesystem-mcp", mode="DO")
    get_connector_mode(user_id="alice", connector="filesystem-mcp")
    get_connector_mode(user_id="alice", connector="email-mcp")
    get_connector_mode(user_id="bob", connector="filesystem-mcp")
    assert {("alice", "filesystem-mcp"), ("alice", "email-mcp"), ("bob", "filesystem-mcp")} <= set(
        _mode_cache.keys()
    )
    clear_cache_for_user("alice")
    assert ("alice", "filesystem-mcp") not in _mode_cache
    assert ("alice", "email-mcp") not in _mode_cache
    assert ("bob", "filesystem-mcp") in _mode_cache


def test_get_all_permissions_returns_user_id_field(perm_store):
    """Plan test 9."""
    from permissions import get_all_permissions

    _seed_perm(perm_store, user_id="alice", connector="filesystem-mcp", mode="DO")
    _seed_perm(perm_store, user_id="bob", connector="email-mcp", mode="ASK")
    rows = get_all_permissions()
    assert all({"user_id", "connector", "mode"} <= row.keys() for row in rows)
    assert [(r["user_id"], r["connector"]) for r in rows] == sorted(
        (r["user_id"], r["connector"]) for r in rows
    )


def test_seed_defaults_is_noop_after_per_user_lift(perm_store, caplog):
    """Plan test 10."""
    from permissions import seed_defaults

    with caplog.at_level(logging.INFO, logger="permissions"):
        seed_defaults()
    assert not perm_store.cp
    assert any("seed_defaults" in r.getMessage() for r in caplog.records)


def test_routine_check_increments_per_user_row_only(perm_store, monkeypatch):
    """Plan test 11."""
    fired: list[dict] = []

    def fake_fire(event, **payload):
        fired.append({"event": event, **payload})

    import hooks

    monkeypatch.setattr(hooks, "fire", fake_fire)

    from permissions import routine_check

    for _ in range(16):
        routine_check(
            user_id="alice",
            connector="filesystem-mcp",
            action_type="write_file",
        )
    alice_row = perm_store.rdt[("alice", "filesystem-mcp", "write_file")]
    assert alice_row["approval_count"] == 16
    assert ("bob", "filesystem-mcp", "write_file") not in perm_store.rdt
    assert sum(1 for f in fired if f.get("user_id") == "alice") >= 1


def test_elevate_to_routine_writes_per_user_row(perm_store):
    """Plan test 12."""
    from permissions import elevate_to_routine

    elevate_to_routine(
        user_id="alice",
        connector="filesystem-mcp",
        action_type="write_file",
    )
    row = perm_store.rdt[("alice", "filesystem-mcp", "write_file")]
    assert row["auto_approved"] is True
    assert ("bob", "filesystem-mcp", "write_file") not in perm_store.rdt


# ---------------------------------------------------------------------------
# Route tests — FastAPI TestClient against the real app.
# ---------------------------------------------------------------------------


def test_me_get_permissions_returns_default_for_unwritten_connectors(
    perm_store,
    auth_env,
    monkeypatch,
):
    """Plan test 13 — every known connector comes back as is_default=True."""
    _stub_registry(monkeypatch, ["filesystem-mcp", "email-mcp"])
    alice_id = _create_user(perm_store, user_id="alice", email="alice@home.lan", role="user")
    with _booted_client() as client:
        r = client.get("/api/v1/me/permissions", headers=_hdr(alice_id))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body and all(row["is_default"] is True for row in body)
    assert all(row["mode"] == "ASK" for row in body)
    assert all(row["updated_at"] is None for row in body)


def test_me_get_permission_for_unwritten_connector_returns_default(
    perm_store,
    auth_env,
    monkeypatch,
):
    """Plan test 13a."""
    _stub_registry(monkeypatch, ["filesystem-mcp"])
    alice_id = _create_user(perm_store, user_id="alice", email="a@home.lan", role="user")
    with _booted_client() as client:
        r = client.get(
            "/api/v1/me/permissions/filesystem-mcp",
            headers=_hdr(alice_id),
        )
    assert r.status_code == 200, r.text
    assert r.json() == {
        "connector": "filesystem-mcp",
        "mode": "ASK",
        "is_default": True,
        "updated_at": None,
    }


def test_me_get_permission_for_written_connector_returns_row(
    perm_store,
    auth_env,
    monkeypatch,
):
    """Plan test 13b."""
    _stub_registry(monkeypatch, ["filesystem-mcp"])
    alice_id = _create_user(perm_store, user_id="alice", email="a@home.lan", role="user")
    _seed_perm(perm_store, user_id=alice_id, connector="filesystem-mcp", mode="DO")
    with _booted_client() as client:
        r = client.get(
            "/api/v1/me/permissions/filesystem-mcp",
            headers=_hdr(alice_id),
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["mode"] == "DO"
    assert body["is_default"] is False
    assert body["updated_at"] is not None


def test_me_get_permission_returns_404_for_unknown_connector(
    perm_store,
    auth_env,
    monkeypatch,
):
    """Plan test 13c."""
    _stub_registry(monkeypatch, ["filesystem-mcp"])
    alice_id = _create_user(perm_store, user_id="alice", email="a@home.lan", role="user")
    with _booted_client() as client:
        r = client.get(
            "/api/v1/me/permissions/email-mcp",
            headers=_hdr(alice_id),
        )
    assert r.status_code == 404, r.text
    assert r.json()["detail"] == {
        "error": "unknown_connector",
        "connector": "email-mcp",
    }


def test_admin_get_user_permission_for_one_connector_returns_admin_view(
    perm_store,
    auth_env,
    monkeypatch,
):
    """Plan test 13d."""
    _stub_registry(monkeypatch, ["calendar-mcp"])
    admin_id = _create_user(perm_store, user_id="admin1", email="ad@home.lan", role="admin")
    bob_id = _create_user(perm_store, user_id="bob", email="bob@home.lan", role="user")
    _seed_perm(perm_store, user_id=bob_id, connector="calendar-mcp", mode="DO")
    with _booted_client() as client:
        r = client.get(
            f"/api/v1/admin/users/{bob_id}/permissions/calendar-mcp",
            headers=_hdr(admin_id, role="admin"),
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["mode"] == "DO"
    assert body["is_default"] is False
    assert body["email"] == "bob@home.lan"
    assert body["user_id"] == bob_id


def test_admin_get_user_permission_returns_404_for_unknown_user(
    perm_store,
    auth_env,
    monkeypatch,
):
    """Plan test 13e."""
    _stub_registry(monkeypatch, ["filesystem-mcp"])
    admin_id = _create_user(perm_store, user_id="admin1", email="ad@home.lan", role="admin")
    with _booted_client() as client:
        r = client.get(
            "/api/v1/admin/users/missing-uuid/permissions/filesystem-mcp",
            headers=_hdr(admin_id, role="admin"),
        )
    assert r.status_code == 404, r.text
    assert r.json()["detail"] == "user not found"


def test_me_put_permission_writes_callers_row(
    perm_store,
    auth_env,
    monkeypatch,
):
    """Plan test 14."""
    _stub_registry(monkeypatch, ["filesystem-mcp"])
    alice_id = _create_user(perm_store, user_id="alice", email="a@home.lan", role="user")
    _create_user(perm_store, user_id="bob", email="b@home.lan", role="user")
    with _booted_client() as client:
        r = client.put(
            "/api/v1/me/permissions/filesystem-mcp",
            headers=_hdr(alice_id),
            json={"mode": "DO"},
        )
    assert r.status_code == 200, r.text
    assert r.json()["mode"] == "DO"
    assert r.json()["is_default"] is False
    assert (alice_id, "filesystem-mcp") in perm_store.cp
    assert ("bob", "filesystem-mcp") not in perm_store.cp


def test_me_put_permission_rejects_unknown_field(
    perm_store,
    auth_env,
    monkeypatch,
):
    """Plan test 15."""
    _stub_registry(monkeypatch, ["filesystem-mcp"])
    alice_id = _create_user(perm_store, user_id="alice", email="a@home.lan", role="user")
    with _booted_client() as client:
        r = client.put(
            "/api/v1/me/permissions/filesystem-mcp",
            headers=_hdr(alice_id),
            json={"mode": "DO", "sneaky": "field"},
        )
    assert r.status_code == 422


def test_me_put_permission_rejects_invalid_mode(
    perm_store,
    auth_env,
    monkeypatch,
):
    """Plan test 16."""
    _stub_registry(monkeypatch, ["filesystem-mcp"])
    alice_id = _create_user(perm_store, user_id="alice", email="a@home.lan", role="user")
    with _booted_client() as client:
        r = client.put(
            "/api/v1/me/permissions/filesystem-mcp",
            headers=_hdr(alice_id),
            json={"mode": "MAYBE"},
        )
    assert r.status_code == 422


def test_me_put_permission_returns_404_for_unknown_connector_when_registry_reachable(
    perm_store,
    auth_env,
    monkeypatch,
):
    """Plan test 17."""
    _stub_registry(monkeypatch, ["filesystem-mcp"])
    alice_id = _create_user(perm_store, user_id="alice", email="a@home.lan", role="user")
    with _booted_client() as client:
        r = client.put(
            "/api/v1/me/permissions/email-mcp",
            headers=_hdr(alice_id),
            json={"mode": "DO"},
        )
    assert r.status_code == 404, r.text
    assert r.json()["detail"]["error"] == "unknown_connector"


def test_me_put_permission_warns_when_registry_unavailable(
    perm_store,
    auth_env,
    monkeypatch,
    caplog,
):
    """Plan test 18 — degrade-allow when actions.registry raises."""
    import actions.registry as _registry

    def boom():
        raise RuntimeError("registry down")

    monkeypatch.setattr(_registry, "list_actions", boom)
    alice_id = _create_user(perm_store, user_id="alice", email="a@home.lan", role="user")
    with caplog.at_level(logging.WARNING, logger="routes.connector_permissions"):
        with _booted_client() as client:
            r = client.put(
                "/api/v1/me/permissions/some-new-connector",
                headers=_hdr(alice_id),
                json={"mode": "DO"},
            )
    assert r.status_code == 200, r.text
    assert "Warning" in r.headers
    assert "199" in r.headers["Warning"]


def test_me_delete_permission_resets_to_default(
    perm_store,
    auth_env,
    monkeypatch,
):
    """Plan test 19."""
    _stub_registry(monkeypatch, ["filesystem-mcp"])
    alice_id = _create_user(perm_store, user_id="alice", email="a@home.lan", role="user")
    _seed_perm(perm_store, user_id=alice_id, connector="filesystem-mcp", mode="DO")
    with _booted_client() as client:
        r = client.delete(
            "/api/v1/me/permissions/filesystem-mcp",
            headers=_hdr(alice_id),
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["is_default"] is True
    assert body["mode"] == "ASK"
    assert body["updated_at"] is None
    assert (alice_id, "filesystem-mcp") not in perm_store.cp


def test_admin_put_other_users_permission_succeeds(
    perm_store,
    auth_env,
    monkeypatch,
):
    """Plan test 20."""
    _stub_registry(monkeypatch, ["filesystem-mcp"])
    admin_id = _create_user(perm_store, user_id="admin1", email="ad@home.lan", role="admin")
    bob_id = _create_user(perm_store, user_id="bob", email="bob@home.lan", role="user")
    with _booted_client() as client:
        r = client.put(
            f"/api/v1/admin/users/{bob_id}/permissions/filesystem-mcp",
            headers=_hdr(admin_id, role="admin"),
            json={"mode": "DO"},
        )
    assert r.status_code == 200, r.text
    assert (bob_id, "filesystem-mcp") in perm_store.cp


def test_admin_put_returns_404_for_unknown_target_user(
    perm_store,
    auth_env,
    monkeypatch,
):
    """Plan test 21."""
    _stub_registry(monkeypatch, ["filesystem-mcp"])
    admin_id = _create_user(perm_store, user_id="admin1", email="ad@home.lan", role="admin")
    with _booted_client() as client:
        r = client.put(
            "/api/v1/admin/users/missing/permissions/filesystem-mcp",
            headers=_hdr(admin_id, role="admin"),
            json={"mode": "DO"},
        )
    assert r.status_code == 404, r.text
    assert r.json()["detail"] == "user not found"


def test_user_cannot_set_other_users_permission_via_admin_route(
    perm_store,
    auth_env,
    monkeypatch,
):
    """Plan test 22 — non-admin caller hits the admin-on-behalf PUT."""
    _stub_registry(monkeypatch, ["filesystem-mcp"])
    alice_id = _create_user(perm_store, user_id="alice", email="a@home.lan", role="user")
    bob_id = _create_user(perm_store, user_id="bob", email="b@home.lan", role="user")
    with _booted_client() as client:
        r = client.put(
            f"/api/v1/admin/users/{bob_id}/permissions/filesystem-mcp",
            headers=_hdr(alice_id),
            json={"mode": "DO"},
        )
    assert r.status_code == 403, r.text


def test_admin_get_all_permissions_returns_user_id_field_and_email(
    perm_store,
    auth_env,
    monkeypatch,
):
    """Plan test 23."""
    admin_id = _create_user(perm_store, user_id="admin1", email="ad@home.lan", role="admin")
    alice_id = _create_user(perm_store, user_id="alice", email="alice@home.lan", role="user")
    bob_id = _create_user(perm_store, user_id="bob", email="bob@home.lan", role="user")
    _seed_perm(perm_store, user_id=alice_id, connector="filesystem-mcp", mode="DO")
    _seed_perm(perm_store, user_id=bob_id, connector="email-mcp", mode="ASK")
    with _booted_client() as client:
        r = client.get(
            "/api/v1/admin/permissions",
            headers=_hdr(admin_id, role="admin"),
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body) == 2
    assert {row["user_id"] for row in body} == {alice_id, bob_id}
    assert all("email" in row and "connector" in row and "mode" in row for row in body)


def test_legacy_get_permissions_requires_admin_now(
    perm_store,
    auth_env,
    monkeypatch,
):
    """Plan test 24."""
    alice_id = _create_user(perm_store, user_id="alice", email="a@home.lan", role="user")
    with _booted_client() as client:
        r = client.get("/permissions", headers=_hdr(alice_id))
    assert r.status_code == 403, r.text


def test_legacy_put_permissions_writes_caller_admin_row_with_deprecation_header(
    perm_store,
    auth_env,
    monkeypatch,
):
    """Plan test 25."""
    admin_id = _create_user(perm_store, user_id="admin1", email="ad@home.lan", role="admin")
    _create_user(perm_store, user_id="bob", email="b@home.lan", role="user")
    with _booted_client() as client:
        r = client.put(
            "/permissions/filesystem-mcp",
            headers=_hdr(admin_id, role="admin"),
            json={"mode": "do"},
        )
    assert r.status_code == 200, r.text
    assert r.headers.get("Deprecation") == "true"
    link = r.headers.get("Link", "")
    assert "/api/v1/me/permissions/filesystem-mcp" in link
    assert 'rel="successor-version"' in link
    assert (admin_id, "filesystem-mcp") in perm_store.cp
    assert ("bob", "filesystem-mcp") not in perm_store.cp


def test_legacy_get_permissions_emits_deprecation_header(
    perm_store,
    auth_env,
    monkeypatch,
):
    """Plan test 26."""
    admin_id = _create_user(perm_store, user_id="admin1", email="ad@home.lan", role="admin")
    with _booted_client() as client:
        r = client.get("/permissions", headers=_hdr(admin_id, role="admin"))
    assert r.status_code == 200, r.text
    assert r.headers.get("Deprecation") == "true"
    link = r.headers.get("Link", "")
    assert "/api/v1/admin/permissions" in link
    assert 'rel="successor-version"' in link


def test_actions_elevate_route_requires_user_auth(
    perm_store,
    auth_env,
    monkeypatch,
):
    """Plan test 27 — closes the unauthenticated-elevation hole."""
    with _booted_client() as client:
        r = client.post(
            "/permissions/filesystem-mcp/elevate",
            json={"action_type": "write_file"},
        )
    assert r.status_code == 401, r.text


def test_actions_elevate_route_threads_user_id_into_routine(
    perm_store,
    auth_env,
    monkeypatch,
):
    """Plan test 28."""
    alice_id = _create_user(perm_store, user_id="alice", email="a@home.lan", role="user")
    _create_user(perm_store, user_id="bob", email="b@home.lan", role="user")
    with _booted_client() as client:
        r = client.post(
            "/permissions/filesystem-mcp/elevate",
            headers=_hdr(alice_id),
            json={"action_type": "write_file"},
        )
    assert r.status_code == 200, r.text
    assert (alice_id, "filesystem-mcp", "write_file") in perm_store.rdt
    assert perm_store.rdt[(alice_id, "filesystem-mcp", "write_file")]["auto_approved"] is True
    assert ("bob", "filesystem-mcp", "write_file") not in perm_store.rdt


def test_actions_elevate_route_still_rejects_hard_limited_action_types(
    perm_store,
    auth_env,
    monkeypatch,
):
    """Plan test 29."""
    alice_id = _create_user(perm_store, user_id="alice", email="a@home.lan", role="user")
    with _booted_client() as client:
        r = client.post(
            "/permissions/filesystem-mcp/elevate",
            headers=_hdr(alice_id),
            json={"action_type": "financial_transaction"},
        )
    assert r.status_code == 403, r.text


def test_disable_user_clears_cache_but_retains_rows(perm_store):
    """Plan test 30."""
    from permissions import _mode_cache
    from permissions import get_connector_mode

    from services import users as users_svc

    _create_user(perm_store, user_id="alice", email="a@home.lan", role="user")
    _seed_perm(perm_store, user_id="alice", connector="filesystem-mcp", mode="DO")
    get_connector_mode(user_id="alice", connector="filesystem-mcp")
    assert ("alice", "filesystem-mcp") in _mode_cache
    users_svc.set_disabled("alice", disabled=True)
    assert ("alice", "filesystem-mcp") not in _mode_cache
    assert ("alice", "filesystem-mcp") in perm_store.cp


def test_delete_user_clears_cache_but_retains_rows(perm_store):
    """Plan test 31."""
    from permissions import _mode_cache
    from permissions import get_connector_mode

    from services import users as users_svc

    _create_user(perm_store, user_id="alice", email="a@home.lan", role="user")
    _seed_perm(perm_store, user_id="alice", connector="filesystem-mcp", mode="DO")
    get_connector_mode(user_id="alice", connector="filesystem-mcp")
    assert ("alice", "filesystem-mcp") in _mode_cache
    users_svc.delete_user("alice")
    assert all(key[0] != "alice" for key in _mode_cache.keys())
    # Forensic retention: connector_permissions row stays in DB even
    # after the owning user is hard-deleted (mirrors mcp_token_user_map D7).
    assert ("alice", "filesystem-mcp") in perm_store.cp


def test_routine_do_tracking_in_user_export_tables():
    """Plan test 32."""
    from services import user_export

    assert "routine_do_tracking" in user_export._USER_EXPORT_TABLES
    assert "routine_do_tracking" in user_export._SERIAL_PK_TABLES


def test_routine_do_tracking_not_in_intentional_exclusions():
    """Plan test 33."""
    from tests.test_user_export_tables_exhaustive import _INTENTIONAL_EXCLUSIONS

    assert "routine_do_tracking" not in _INTENTIONAL_EXCLUSIONS


def _scoped_tables_from_source() -> frozenset[str]:
    """Parse ``_SCOPED_TABLES`` from ``db_default_user_remap.py`` without importing it.

    Importing the module requires ``psycopg2``, which is not always installed in
    lightweight dev venvs — these assertions only need the static tuple contents.
    """
    import ast
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "db_default_user_remap.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.target.id != "_SCOPED_TABLES" or not isinstance(node.value, ast.Tuple):
                continue
            names = []
            for elt in node.value.elts:
                if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                    names.append(elt.value)
            return frozenset(names)
    raise AssertionError("_SCOPED_TABLES tuple not found in db_default_user_remap.py")


def test_db_default_user_remap_includes_user_batch_jobs():
    assert "user_batch_jobs" in _scoped_tables_from_source()


def test_db_default_user_remap_includes_routine_do_tracking():
    """Plan test 34."""
    assert "routine_do_tracking" in _scoped_tables_from_source()
