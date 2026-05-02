# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Password management: ``POST /api/v1/me/password``, admin reset, CLI/service."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

import services.users as users_svc
from tests.test_auth_phase1 import auth_env
from tests.test_auth_phase1 import dev_env
from tests.test_auth_phase1 import users_store
from tests.test_auth_phase2 import _admin_headers
from tests.test_auth_phase2 import _client
from tests.test_auth_phase2 import _user_headers


def _seed_admin_if_needed() -> None:
    if users_svc.get_user_by_email("admin@home.lan") is None:
        users_svc.create_user("admin@home.lan", "verylongpassword12", "admin")


def test_me_password_unauthenticated_401(users_store, auth_env):
    import main

    with TestClient(main.app) as client:
        r = client.post(
            "/api/v1/me/password",
            json={
                "current_password": "verylongpassword12",
                "new_password": "newlongpassword12x",
            },
        )
    assert r.status_code == 401


def test_me_password_unavailable_in_dev_mode(users_store, dev_env):
    import main

    with TestClient(main.app) as client:
        r = client.post(
            "/api/v1/me/password",
            json={
                "current_password": "verylongpassword12",
                "new_password": "newlongpassword12x",
            },
        )
    assert r.status_code == 503
    assert "dev mode" in r.json()["detail"]


def test_me_password_happy_path(users_store, auth_env):
    with _client(users_store) as client:
        r = client.post(
            "/api/v1/me/password",
            headers=_admin_headers(users_store),
            json={
                "current_password": "verylongpassword12",
                "new_password": "brandnewpassword12",
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert body == {"ok": True}
        assert "password" not in json.dumps(body)
        u = users_svc.get_user_by_email("admin@home.lan")
        assert u is not None
        assert users_svc.verify_credentials("admin@home.lan", "brandnewpassword12") is not None
        assert users_svc.verify_credentials("admin@home.lan", "verylongpassword12") is None


def test_me_password_wrong_current_403(users_store, auth_env):
    with _client(users_store) as client:
        r = client.post(
            "/api/v1/me/password",
            headers=_admin_headers(users_store),
            json={
                "current_password": "notthepassword12",
                "new_password": "brandnewpassword12",
            },
        )
    assert r.status_code == 403
    assert r.json()["detail"] == "invalid credentials"


def test_me_password_short_new_400(users_store, auth_env):
    with _client(users_store) as client:
        r = client.post(
            "/api/v1/me/password",
            headers=_admin_headers(users_store),
            json={
                "current_password": "verylongpassword12",
                "new_password": "short",
            },
        )
    assert r.status_code == 400
    assert "12" in r.json()["detail"]


def test_me_password_same_as_current_400(users_store, auth_env):
    with _client(users_store) as client:
        r = client.post(
            "/api/v1/me/password",
            headers=_admin_headers(users_store),
            json={
                "current_password": "verylongpassword12",
                "new_password": "verylongpassword12",
            },
        )
    assert r.status_code == 400


def test_me_password_clears_refresh_jti(users_store, auth_env):
    with _client(users_store) as client:
        login = client.post(
            "/api/v1/auth/login",
            json={"email": "admin@home.lan", "password": "verylongpassword12"},
        )
        assert login.status_code == 200
        admin = users_svc.get_user_by_email("admin@home.lan")
        assert admin is not None
        assert users_svc.get_refresh_jti(admin.id) is not None
        r = client.post(
            "/api/v1/me/password",
            headers=_admin_headers(users_store),
            json={
                "current_password": "verylongpassword12",
                "new_password": "anothernewpassword12",
            },
        )
        assert r.status_code == 200
        assert users_svc.get_refresh_jti(admin.id) is None


def test_admin_reset_password_unauthenticated_401(users_store, auth_env):
    import main

    with TestClient(main.app) as client:
        r = client.post(
            "/api/v1/admin/users/some-id/password",
            json={"new_password": "brandnewpassword12"},
        )
    assert r.status_code == 401


def test_admin_reset_password_forbidden_for_user_role(users_store, auth_env):
    users_svc.create_user("target@home.lan", "verylongpassword12", "user")
    target = users_svc.get_user_by_email("target@home.lan")
    assert target is not None
    with _client(users_store) as client:
        r = client.post(
            f"/api/v1/admin/users/{target.id}/password",
            headers=_user_headers(users_store),
            json={"new_password": "resetpassword123x"},
        )
    assert r.status_code == 403


def test_admin_reset_password_happy_path(users_store, auth_env):
    target = users_svc.create_user("target@home.lan", "verylongpassword12", "user")
    with _client(users_store) as client:
        r = client.post(
            f"/api/v1/admin/users/{target.id}/password",
            headers=_admin_headers(users_store),
            json={"new_password": "resetlongpassword12"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body == {"ok": True}
        assert "hash" not in json.dumps(body).lower()
        assert users_svc.verify_credentials("target@home.lan", "resetlongpassword12") is not None
        assert users_svc.verify_credentials("target@home.lan", "verylongpassword12") is None


def test_admin_reset_password_404(users_store, auth_env):
    with _client(users_store) as client:
        r = client.post(
            "/api/v1/admin/users/missing-user-id/password",
            headers=_admin_headers(users_store),
            json={"new_password": "resetlongpassword12"},
        )
    assert r.status_code == 404


def test_admin_reset_password_short_400(users_store, auth_env):
    target = users_svc.create_user("target2@home.lan", "verylongpassword12", "user")
    with _client(users_store) as client:
        r = client.post(
            f"/api/v1/admin/users/{target.id}/password",
            headers=_admin_headers(users_store),
            json={"new_password": "short"},
        )
    assert r.status_code == 400


def test_admin_reset_password_disabled_user(users_store, auth_env):
    with _client(users_store) as client:
        admin = users_svc.get_user_by_email("admin@home.lan")
        assert admin is not None
        target = users_svc.create_user("dis@home.lan", "verylongpassword12", "user")
        users_svc.set_disabled(target.id, True, by_admin_user_id=admin.id)
        r = client.post(
            f"/api/v1/admin/users/{target.id}/password",
            headers=_admin_headers(users_store),
            json={"new_password": "newpwfordisabled12"},
        )
        assert r.status_code == 200
        assert users_svc.verify_credentials("dis@home.lan", "newpwfordisabled12") is None


def test_cli_reset_password_by_email(users_store, auth_env):
    _seed_admin_if_needed()
    users_svc.cli_reset_password(
        email="admin@home.lan",
        user_id=None,
        new_password="cliresetpassword12",
    )
    assert users_svc.verify_credentials("admin@home.lan", "cliresetpassword12") is not None


def test_cli_reset_password_by_user_id(users_store, auth_env):
    _seed_admin_if_needed()
    admin = users_svc.get_user_by_email("admin@home.lan")
    assert admin is not None
    users_svc.cli_reset_password(
        email=None,
        user_id=admin.id,
        new_password="cliidresetpassword12",
    )
    assert users_svc.verify_credentials("admin@home.lan", "cliidresetpassword12") is not None


def test_cli_reset_password_missing_user(users_store, auth_env):
    with pytest.raises(LookupError):
        users_svc.cli_reset_password(
            email="nobody@home.lan",
            user_id=None,
            new_password="somepassword123x",
        )


def test_cli_reset_password_policy(users_store, auth_env):
    _seed_admin_if_needed()
    with pytest.raises(users_svc.PasswordPolicyViolationError):
        users_svc.cli_reset_password(
            email="admin@home.lan",
            user_id=None,
            new_password="short",
        )


def test_reset_password_script_main(users_store, auth_env):
    from scripts.reset_password import main

    _seed_admin_if_needed()
    code = main(["--email", "admin@home.lan", "--password", "scriptresetpw12x"])
    assert code == 0
    assert users_svc.verify_credentials("admin@home.lan", "scriptresetpw12x") is not None
