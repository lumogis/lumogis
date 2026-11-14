# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis

"""LUM-918 (security audit #2 ID-009): ``tv`` required by default, access TTL clamped."""

from __future__ import annotations

import logging
import os
import time

import auth
import jwt
import pytest
from fastapi.testclient import TestClient
from tests.test_auth_phase1 import _csrf_origin_headers

pytest_plugins = ("tests.test_auth_phase1",)


def _typless_tv_token(sub: str) -> str:
    """An access JWT with every claim except ``tv`` / ``sid`` (a hand-minted legacy shape)."""
    now = int(time.time())
    return jwt.encode(
        {"sub": sub, "typ": "access", "role": "admin", "iat": now, "exp": now + 600},
        os.environ["AUTH_SECRET"],
        algorithm="HS256",
    )


# ---------------------------------------------------------------------------
# Flag defaults
# ---------------------------------------------------------------------------


def test_require_tv_claim_defaults_on(monkeypatch):
    monkeypatch.delenv("LUMOGIS_REQUIRE_TV_CLAIM", raising=False)
    assert auth.require_tv_claim() is True


@pytest.mark.parametrize("raw", ["false", "FALSE", "0", "no", " false "])
def test_require_tv_claim_explicit_off(monkeypatch, raw):
    monkeypatch.setenv("LUMOGIS_REQUIRE_TV_CLAIM", raw)
    assert auth.require_tv_claim() is False


@pytest.mark.parametrize("raw", ["true", "1", "yes", ""])
def test_require_tv_claim_other_values_stay_on(monkeypatch, raw):
    monkeypatch.setenv("LUMOGIS_REQUIRE_TV_CLAIM", raw)
    assert auth.require_tv_claim() is True


def test_sid_revocation_check_still_defaults_off(monkeypatch):
    # Kept off deliberately (see require_sid_revocation_check docstring): a refresh
    # rotates the session row, so on-by-default would sign out sibling browser tabs.
    monkeypatch.delenv("LUMOGIS_REQUIRE_SID_REVOCATION_CHECK", raising=False)
    assert auth.require_sid_revocation_check() is False


def test_missing_tv_rejected_by_default(users_store, auth_env, monkeypatch):
    monkeypatch.delenv("LUMOGIS_REQUIRE_TV_CLAIM", raising=False)
    import main
    import services.users as users_svc

    users_svc.create_user("tv-default@home.lan", "verylongpassword12", "admin")
    u = users_svc.get_user_by_email("tv-default@home.lan")
    assert u is not None
    with TestClient(main.app) as client:
        resp = client.get(
            "/api/v1/auth/me", headers={"Authorization": f"Bearer {_typless_tv_token(u.id)}"}
        )
    assert resp.status_code == 401


def test_missing_tv_accepted_when_operator_opts_out(users_store, auth_env, monkeypatch):
    monkeypatch.setenv("LUMOGIS_REQUIRE_TV_CLAIM", "false")
    import main
    import services.users as users_svc

    users_svc.create_user("tv-optout@home.lan", "verylongpassword12", "admin")
    u = users_svc.get_user_by_email("tv-optout@home.lan")
    assert u is not None
    with TestClient(main.app) as client:
        resp = client.get(
            "/api/v1/auth/me", headers={"Authorization": f"Bearer {_typless_tv_token(u.id)}"}
        )
    assert resp.status_code == 200


def test_login_and_refresh_tokens_pass_the_default_gate(users_store, auth_env, monkeypatch):
    """The production mint path (login, then refresh) carries ``sid`` and ``tv``."""
    monkeypatch.delenv("LUMOGIS_REQUIRE_TV_CLAIM", raising=False)
    import main
    import services.users as users_svc

    users_svc.create_user("tv-login@home.lan", "verylongpassword12", "admin")
    with TestClient(main.app) as client:
        login = client.post(
            "/api/v1/auth/login",
            json={"email": "tv-login@home.lan", "password": "verylongpassword12"},
        )
        assert login.status_code == 200, login.text
        access = login.json()["access_token"]
        claims = jwt.decode(access, options={"verify_signature": False})
        assert isinstance(claims.get("sid"), str) and "tv" in claims
        me = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {access}"})
        assert me.status_code == 200

        cookie = login.cookies.get("lumogis_refresh")
        assert cookie
        client.cookies.clear()
        refreshed = client.post(
            "/api/v1/auth/refresh",
            cookies={"lumogis_refresh": cookie},
            headers=_csrf_origin_headers(),
        )
        assert refreshed.status_code == 200, refreshed.text
        access2 = refreshed.json()["access_token"]
        claims2 = jwt.decode(access2, options={"verify_signature": False})
        assert isinstance(claims2.get("sid"), str) and "tv" in claims2
        me2 = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {access2}"})
        assert me2.status_code == 200


def test_auth_off_ignores_tv_gate(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.delenv("LUMOGIS_REQUIRE_TV_CLAIM", raising=False)
    assert auth.jwt_revocation_failure_reason({"sub": "default"}) is None


# ---------------------------------------------------------------------------
# ACCESS_TOKEN_TTL_SECONDS clamp
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        (None, 900),
        ("900", 900),
        ("3600", 3600),
        ("3601", 3600),
        ("86400", 3600),
        ("30", 60),
        ("-5", 60),
        ("not-a-number", 900),
    ],
)
def test_access_token_ttl_clamped(monkeypatch, raw, expected):
    if raw is None:
        monkeypatch.delenv("ACCESS_TOKEN_TTL_SECONDS", raising=False)
    else:
        monkeypatch.setenv("ACCESS_TOKEN_TTL_SECONDS", raw)
    assert auth.access_token_ttl_seconds() == expected


def test_minted_token_exp_respects_clamp(monkeypatch):
    monkeypatch.setenv("AUTH_SECRET", "x" * 64)
    monkeypatch.setenv("ACCESS_TOKEN_TTL_SECONDS", str(7 * 24 * 3600))
    token = auth.mint_access_token("u1", "user", session_id="s1", token_version=0)
    claims = jwt.decode(token, options={"verify_signature": False})
    assert claims["exp"] - claims["iat"] == auth.ACCESS_TOKEN_TTL_MAX_SECONDS


def test_ttl_warning_fires_above_cap(monkeypatch, caplog):
    monkeypatch.setenv("ACCESS_TOKEN_TTL_SECONDS", "86400")
    with caplog.at_level(logging.WARNING, logger="auth"):
        assert auth.warn_if_access_token_ttl_clamped() is True
    msgs = [r.getMessage() for r in caplog.records if r.name == "auth"]
    assert any("ACCESS_TOKEN_TTL_SECONDS=86400" in m and "3600" in m for m in msgs)


@pytest.mark.parametrize("raw", [None, "900", "3600", "30", "garbage"])
def test_ttl_warning_silent_within_cap(monkeypatch, caplog, raw):
    if raw is None:
        monkeypatch.delenv("ACCESS_TOKEN_TTL_SECONDS", raising=False)
    else:
        monkeypatch.setenv("ACCESS_TOKEN_TTL_SECONDS", raw)
    with caplog.at_level(logging.WARNING, logger="auth"):
        assert auth.warn_if_access_token_ttl_clamped() is False
    assert not [r for r in caplog.records if r.name == "auth"]


def test_boot_logs_ttl_warning(monkeypatch, caplog):
    monkeypatch.setenv("ACCESS_TOKEN_TTL_SECONDS", "86400")
    import main

    with caplog.at_level(logging.WARNING, logger="auth"):
        with TestClient(main.app):
            pass
    assert any(
        "ACCESS_TOKEN_TTL_SECONDS=86400" in r.getMessage()
        for r in caplog.records
        if r.name == "auth"
    )
