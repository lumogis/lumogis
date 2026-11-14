# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Account-state cascade on a real Postgres (LUM-885).

``FakeUsersStore`` does not model rollback or SQL semantics, so these are the
proof of (a) the ``mcp_tokens`` / ``user_invites`` JOINs on the real schema,
(b) the end-to-end cascades through ``TestClient``, (c) the 401 → refresh upgrade
path, and (d) atomicity: each rollback test fails the **last** helper step, so
the earlier statements must roll back for it to pass.

Skips when Postgres is unreachable (plain host without a stack). Under
``make compose-test`` Postgres is a ``depends_on`` service and these run for real.
"""

from __future__ import annotations

import time
import uuid
from contextlib import contextmanager

import jwt
import pytest
from fastapi.testclient import TestClient
from tests.integration._pgguard import require_postgres

import config

PW = "verylongpassword12"
NEW_PW = "brandnewpassword12"
ACCESS_SECRET = "cascade-live-access-secret"
MCP_INIT = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-03-26",
        "capabilities": {},
        "clientInfo": {"name": "lum885", "version": "0"},
    },
}


@pytest.fixture
def auth_env(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTH_SECRET", ACCESS_SECRET)
    monkeypatch.setenv("LUMOGIS_JWT_REFRESH_SECRET", "cascade-live-refresh-secret")
    monkeypatch.setenv("ACCESS_TOKEN_TTL_SECONDS", "900")
    monkeypatch.setenv("REFRESH_TOKEN_TTL_SECONDS", "3600")
    monkeypatch.setenv("LUMOGIS_REFRESH_COOKIE_SECURE", "false")
    monkeypatch.delenv("LUMOGIS_PUBLIC_ORIGIN", raising=False)
    monkeypatch.delenv("MCP_AUTH_TOKEN", raising=False)
    yield
    from routes.auth import _reset_rate_limit_for_tests
    from routes.invites import _reset_limiters_for_tests

    _reset_rate_limit_for_tests()
    _reset_limiters_for_tests()


def _connect():
    """A new ``PostgresStore``. The app lifespan closes the store it was given, so
    anything that must query after the app exits (teardown) opens its own."""
    import os

    from adapters.postgres_store import PostgresStore

    return PostgresStore(
        host=os.environ.get("POSTGRES_HOST", "postgres"),
        port=int(os.environ.get("POSTGRES_PORT", "5432")),
        user=os.environ.get("POSTGRES_USER", "lumogis"),
        password=os.environ.get("POSTGRES_PASSWORD", "lumogis-dev"),
        dbname=os.environ.get("POSTGRES_DB", "lumogis"),
    )


@pytest.fixture
def live_postgres(monkeypatch, auth_env):
    try:
        ms = _connect()
        if not ms.ping():
            raise RuntimeError("postgres ping failed")
    except Exception as exc:  # pragma: no cover - env-dependent
        require_postgres(exc, what="Postgres")

    monkeypatch.setitem(config._instances, "metadata_store", ms)
    yield ms


class Cast:
    """Users created by one test, removed (with what hangs off them) on teardown."""

    def __init__(self, ms) -> None:
        self.ms = ms
        self.ids: list[str] = []
        self.suffix = uuid.uuid4().hex[:8]

    def user(self, name: str, role: str = "user") -> tuple[str, str]:
        import services.users as users_svc

        email = f"lum885-{name}-{self.suffix}@home.lan"
        uid = users_svc.create_user(email, PW, role).id
        self.ids.append(uid)
        return uid, email

    def cleanup(self) -> None:
        self.ms = _connect()
        for uid in self.ids:
            for stmt in (
                "DELETE FROM password_reset_tokens WHERE user_id = %s",
                "DELETE FROM user_invites WHERE created_by = %s",
                "DELETE FROM mcp_tokens WHERE user_id = %s",
                "DELETE FROM auth_sessions WHERE user_id = %s",
                "DELETE FROM users WHERE id = %s",
            ):
                try:
                    self.ms.execute(stmt, (uid,))
                except Exception:
                    pass


@pytest.fixture
def cast(live_postgres):
    c = Cast(live_postgres)
    yield c
    c.cleanup()


@contextmanager
def _app():
    import main

    with TestClient(main.app) as client:
        yield client


def _login(client: TestClient, email: str, password: str = PW) -> str:
    r = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, f"login {email}: {r.status_code} {r.text}"
    return r.json()["access_token"]


def _h(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _mint_mcp(client: TestClient, access: str) -> tuple[str, str]:
    r = client.post(
        "/api/v1/me/mcp-tokens", json={"label": "t", "scopes": ["mcp:read"]}, headers=_h(access)
    )
    assert r.status_code in (200, 201), r.text
    body = r.json()
    return body["plaintext"], body["token"]["id"]


def _mcp_status(client: TestClient, plaintext: str) -> int:
    r = client.post(
        "/mcp/",
        json=MCP_INIT,
        headers={
            "Authorization": f"Bearer {plaintext}",
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        },
    )
    return r.status_code


def _scalar(ms, sql: str, params: tuple):
    row = ms.fetch_one(sql, params)
    assert row is not None, (sql, params)
    return next(iter(row.values()))


# ---------------------------------------------------------------------------
# L1 / L2: the owner check on the real schema
# ---------------------------------------------------------------------------


def test_l1_deleted_owner_token_is_rejected(cast, live_postgres):
    ms = live_postgres
    m, m_email = cast.user("member")
    _a, a_email = cast.user("admin", "admin")
    with _app() as client:
        m_tok = _login(client, m_email)
        plaintext, token_id = _mint_mcp(client, m_tok)
        assert _mcp_status(client, plaintext) != 401  # negative control: the token works

        a_tok = _login(client, a_email)
        r = client.delete(f"/api/v1/admin/users/{m}", headers=_h(a_tok))
        assert r.status_code == 200, r.text

        assert _mcp_status(client, plaintext) == 401
    ms = _connect()  # the app lifespan closed the injected store
    row = ms.fetch_one("SELECT revoked_at FROM mcp_tokens WHERE id = %s", (token_id,))
    assert row is not None, "ADR 017: the forensic row is kept"
    assert row["revoked_at"] is not None
    assert ms.fetch_one("SELECT id FROM users WHERE id = %s", (m,)) is None


def test_l2_backstop_rejects_when_the_cascade_was_skipped(cast, live_postgres):
    """Raw SQL bypasses the cascade: only the LEFT JOIN in verify() can catch it."""
    ms = live_postgres
    m, m_email = cast.user("member")
    n, n_email = cast.user("other")
    with _app() as client:
        p_m, _ = _mint_mcp(client, _login(client, m_email))
        p_n, _ = _mint_mcp(client, _login(client, n_email))
        assert _mcp_status(client, p_m) != 401 and _mcp_status(client, p_n) != 401

        ms.execute("UPDATE users SET disabled = TRUE WHERE id = %s", (m,))
        assert _mcp_status(client, p_m) == 401  # disabled owner

        ms.execute("DELETE FROM users WHERE id = %s", (n,))
        assert _mcp_status(client, p_n) == 401  # deleted owner (token row not revoked)
    ms = _connect()
    assert (
        ms.fetch_one(
            "SELECT 1 AS x FROM mcp_tokens WHERE user_id = %s AND revoked_at IS NULL", (n,)
        )
        is not None
    )


# ---------------------------------------------------------------------------
# L3 / L4: demotion, password change, logout-all
# ---------------------------------------------------------------------------


def test_l3_demoted_admin_cannot_repromote(cast):
    a, a_email = cast.user("admin", "admin")
    b, b_email = cast.user("admin2", "admin")
    with _app() as client_a, _app() as client_b:
        t_a = _login(client_a, a_email)
        t_old = _login(client_b, b_email)
        ok = client_b.get("/api/v1/admin/users", headers=_h(t_old))
        assert ok.status_code == 200  # negative control

        r = client_a.patch(f"/api/v1/admin/users/{b}", json={"role": "user"}, headers=_h(t_a))
        assert r.status_code == 200, r.text

        assert (
            client_b.patch(
                f"/api/v1/admin/users/{b}", json={"role": "admin"}, headers=_h(t_old)
            ).status_code
            == 401
        )
        assert client_b.post("/api/v1/auth/refresh").status_code == 401

        t_new = _login(client_b, b_email)  # signing in again works, as a member
        assert (
            client_b.patch(
                f"/api/v1/admin/users/{b}", json={"role": "admin"}, headers=_h(t_new)
            ).status_code
            == 403
        )
    del a


def test_l4_password_change_and_logout_all_revoke_mcp_tokens(cast):
    _m, m_email = cast.user("member")
    with _app() as client:
        access = _login(client, m_email)
        token1, _ = _mint_mcp(client, access)
        assert _mcp_status(client, token1) != 401

        r = client.post(
            "/api/v1/me/password",
            json={"current_password": PW, "new_password": NEW_PW},
            headers=_h(access),
        )
        assert r.status_code == 200, r.text
        assert _mcp_status(client, token1) == 401

        access2 = _login(client, m_email, NEW_PW)
        token2, _ = _mint_mcp(client, access2)
        assert _mcp_status(client, token2) != 401
        out = client.post("/api/v1/me/logout-all", headers=_h(access2))
        assert out.status_code == 204
        assert _mcp_status(client, token2) == 401


# ---------------------------------------------------------------------------
# L5: a refresh JWT is not a Bearer, and the upgrade path is one refresh
# ---------------------------------------------------------------------------


def test_l5_refresh_as_bearer_rejected_and_typless_access_refreshes_once(cast):
    m, m_email = cast.user("member")
    with _app() as client:
        access = _login(client, m_email)
        claims = jwt.decode(access, options={"verify_signature": False})
        assert claims["typ"] == "access"
        assert client.get("/api/v1/auth/me", headers=_h(access)).status_code == 200

        cookie = client.cookies.get("lumogis_refresh")
        assert cookie, "login must set the refresh cookie"
        assert client.get("/api/v1/auth/me", headers=_h(cookie)).status_code == 401

        # An access token minted before this release: right secret, real sid/tv, no typ.
        now = int(time.time())
        typeless = jwt.encode(
            {
                "sub": m,
                "role": "user",
                "sid": claims["sid"],
                "tv": claims["tv"],
                "iat": now,
                "exp": now + 600,
            },
            ACCESS_SECRET,
            algorithm="HS256",
        )
        assert client.get("/api/v1/auth/me", headers=_h(typeless)).status_code == 401

        # The Web client's 401 -> refresh -> retry: the cookie still works,
        # and the freshly minted token carries typ.
        refreshed = client.post("/api/v1/auth/refresh")
        assert refreshed.status_code == 200, refreshed.text
        new_access = refreshed.json()["access_token"]
        assert client.get("/api/v1/auth/me", headers=_h(new_access)).status_code == 200


# ---------------------------------------------------------------------------
# L6 / L6b / L6c: atomicity. Fail the LAST helper step; everything before it
# must roll back, or the assertions below fail.
# ---------------------------------------------------------------------------


class _Boom(RuntimeError):
    pass


def _boom(*_a, **_k):
    raise _Boom("injected failure in the last helper step")


def _seed_holdings(cast, ms, uid: str) -> tuple[str, str]:
    """One active MCP token and one active session for ``uid``."""
    from services import auth_sessions as sess
    from services import mcp_tokens

    row, _plain = mcp_tokens.mint(uid, "seeded")
    sid = uuid.uuid4().hex
    ms.execute(
        "INSERT INTO auth_sessions (id, user_id, family_id, refresh_token_hash, expires_at, "
        "device_label, ip_hash, ua_hash) VALUES (%s, %s, %s, %s, NOW() + INTERVAL '1 day', "
        "%s, %s, %s)",
        (sid, uid, uuid.uuid4().hex, "r" * 64, "dev", "i" * 64, "u" * 64),
    )
    del sess
    return row.id, sid


def _assert_nothing_changed(ms, uid, token_id, sid, tv0):
    assert _scalar(ms, "SELECT token_version FROM users WHERE id = %s", (uid,)) == tv0
    assert ms.fetch_one(
        "SELECT 1 AS x FROM mcp_tokens WHERE id = %s AND revoked_at IS NULL", (token_id,)
    )
    assert ms.fetch_one(
        "SELECT 1 AS x FROM auth_sessions WHERE id = %s AND revoked_at IS NULL", (sid,)
    )


def test_l6_demotion_rolls_back_when_the_last_step_fails(cast, live_postgres, monkeypatch):
    import services.users as users_svc

    ms = live_postgres
    a, _ = cast.user("admin", "admin")
    b, _ = cast.user("admin2", "admin")
    token_id, sid = _seed_holdings(cast, ms, b)
    tv0 = _scalar(ms, "SELECT token_version FROM users WHERE id = %s", (b,))

    monkeypatch.setattr("services.user_invites.revoke_unredeemed_for_creator_in_transaction", _boom)
    with pytest.raises(_Boom):
        users_svc.update_role(b, "user", by_admin_user_id=a)

    assert _scalar(ms, "SELECT role FROM users WHERE id = %s", (b,)) == "admin"
    _assert_nothing_changed(ms, b, token_id, sid, tv0)


@pytest.mark.parametrize("path", ["change_own_password", "email_reset_redeem"])
def test_l6b_password_paths_roll_back_when_the_last_step_fails(
    cast, live_postgres, monkeypatch, path
):
    import services.users as users_svc

    from services import password_reset

    ms = live_postgres
    m, _ = cast.user("member")
    token_id, sid = _seed_holdings(cast, ms, m)
    tv0 = _scalar(ms, "SELECT token_version FROM users WHERE id = %s", (m,))
    hash0 = _scalar(ms, "SELECT password_hash FROM users WHERE id = %s", (m,))
    reset_plain = password_reset.mint(ms, m).plaintext if path == "email_reset_redeem" else None

    monkeypatch.setattr("services.auth_sessions.revoke_all_active_in_transaction_for_user", _boom)
    with pytest.raises(_Boom):
        if reset_plain is None:
            users_svc.change_own_password(m, PW, NEW_PW)
        else:
            password_reset.redeem(reset_plain, NEW_PW)

    assert _scalar(ms, "SELECT password_hash FROM users WHERE id = %s", (m,)) == hash0
    _assert_nothing_changed(ms, m, token_id, sid, tv0)
    if reset_plain is not None:
        assert (
            _scalar(
                ms,
                "SELECT COUNT(*) AS n FROM password_reset_tokens "
                "WHERE user_id = %s AND used_at IS NULL",
                (m,),
            )
            == 1
        ), "the reset link must not be burned by a rolled-back redeem"


def test_l6c_delete_rolls_back_when_the_last_step_fails(cast, live_postgres, monkeypatch):
    import services.users as users_svc

    ms = live_postgres
    a, _ = cast.user("admin", "admin")
    m, _ = cast.user("member")
    token_id, sid = _seed_holdings(cast, ms, m)
    tv0 = _scalar(ms, "SELECT token_version FROM users WHERE id = %s", (m,))

    monkeypatch.setattr("services.auth_sessions.revoke_all_active_in_transaction_for_user", _boom)
    with pytest.raises(_Boom):
        users_svc.delete_user(m, by_admin_user_id=a)

    assert ms.fetch_one("SELECT id FROM users WHERE id = %s", (m,)) is not None
    _assert_nothing_changed(ms, m, token_id, sid, tv0)


# ---------------------------------------------------------------------------
# L7: an invite is valid only while its creator is an active admin
# ---------------------------------------------------------------------------


def test_l7_invite_of_a_demoted_creator_cannot_be_redeemed(cast, live_postgres):
    ms = live_postgres
    a, a_email = cast.user("admin", "admin")
    _b, b_email = cast.user("admin2", "admin")
    invitee = f"lum885-invitee-{cast.suffix}@home.lan"
    with _app() as client_a, _app() as client_b:
        t_a = _login(client_a, a_email)
        t_b = _login(client_b, b_email)

        def mint():
            r = client_a.post(
                "/api/v1/admin/users/invites", json={"role": "admin"}, headers=_h(t_a)
            )
            assert r.status_code == 201, r.text
            return r.json()["token"], r.json()["invite"]["id"]

        token1, id1 = mint()
        token2, id2 = mint()
        assert client_a.get(f"/api/v1/invites/{token1}").status_code == 200  # negative control

        r = client_b.patch(f"/api/v1/admin/users/{a}", json={"role": "user"}, headers=_h(t_b))
        assert r.status_code == 200, r.text

        # The cascade revoked the unredeemed invites ...
        assert client_a.get(f"/api/v1/invites/{token1}").status_code == 404
        redeem = client_a.post(
            f"/api/v1/invites/{token1}/redeem", json={"email": invitee, "password": PW}
        )
        assert redeem.status_code == 404
        assert _scalar(ms, "SELECT revoked_at FROM user_invites WHERE id = %s", (id1,)) is not None
        assert (
            ms.fetch_one("SELECT id FROM users WHERE lower(email) = lower(%s)", (invitee,)) is None
        )

        # ... and the redeem-time backstop holds on real SQL when the cascade was skipped.
        ms.execute("UPDATE user_invites SET revoked_at = NULL WHERE id = %s", (id2,))
        assert client_a.get(f"/api/v1/invites/{token2}").status_code == 404
        redeem2 = client_a.post(
            f"/api/v1/invites/{token2}/redeem", json={"email": invitee, "password": PW}
        )
        assert redeem2.status_code == 404
        assert (
            ms.fetch_one("SELECT id FROM users WHERE lower(email) = lower(%s)", (invitee,)) is None
        )


def test_l7b_consume_rechecks_the_creator_after_the_lookup(cast, live_postgres, monkeypatch):
    """A demotion that commits between the lookup and the consume still wins.

    The lookup is pinned to the row it saw while the creator was an admin; the
    creator is then demoted, so only the creator predicate inside the consume
    ``UPDATE`` can refuse the redeem.
    """
    from models.user_invite import InviteInvalidError

    from services import user_invites

    ms = live_postgres
    a, _ = cast.user("admin", "admin")
    invitee = f"lum885-race-{cast.suffix}@home.lan"
    internal, plaintext = user_invites.mint_invite(role="admin", allows_shared=True, created_by=a)

    prefix = plaintext[len("linv_") :][:16]
    stale_row = user_invites._lookup_active_by_prefix(prefix)
    assert stale_row is not None  # negative control: live while the creator is an admin

    ms.execute("UPDATE users SET role = 'user' WHERE id = %s", (a,))
    monkeypatch.setattr(user_invites, "_lookup_active_by_prefix", lambda _p: stale_row)

    with pytest.raises(InviteInvalidError):
        user_invites.redeem_invite(plaintext, invitee, PW)

    assert ms.fetch_one("SELECT id FROM users WHERE lower(email) = lower(%s)", (invitee,)) is None
    assert _scalar(ms, "SELECT used_at FROM user_invites WHERE id = %s", (internal.id,)) is None


# ---------------------------------------------------------------------------
# L8: a refresh racing a password change or "log out everywhere" (LUM-896)
# ---------------------------------------------------------------------------

_RACE_WAIT_S = 10


def _revoke(path: str, uid: str) -> None:
    import services.users as users_svc

    if path == "change_own_password":
        users_svc.change_own_password(uid, PW, NEW_PW)
    else:
        users_svc.revoke_all_credentials(uid)


def _live_sessions(ms, uid: str) -> int:
    return _scalar(
        ms,
        "SELECT COUNT(*) AS n FROM auth_sessions WHERE user_id = %s AND revoked_at IS NULL",
        (uid,),
    )


def _rotate_in_thread(uid: str, cookie: str, out: dict):
    import threading

    from services import auth_sessions as sess

    jti = jwt.decode(cookie, options={"verify_signature": False})["jti"]

    def run():
        try:
            out["result"] = sess.rotate_refresh(
                user_id=uid, jti=jti, refresh_jwt=cookie, refresh_ttl_seconds=3600
            )
        except Exception as exc:  # noqa: BLE001 - the test inspects it
            out["error"] = exc

    return threading.Thread(target=run, name="lum896-rotate", daemon=True)


def _wait_for_lock_waiter(ms) -> bool:
    """True once some backend on this database is waiting on a row lock."""
    deadline = time.monotonic() + _RACE_WAIT_S
    while time.monotonic() < deadline:
        n = _scalar(
            ms,
            "SELECT COUNT(*) AS n FROM pg_stat_activity "
            "WHERE datname = current_database() AND wait_event_type = 'Lock'",
            (),
        )
        if n:
            return True
        time.sleep(0.05)
    return False


def _in_rotation_thread() -> bool:
    import threading

    return threading.current_thread().name == "lum896-rotate"


@pytest.mark.parametrize("path", ["change_own_password", "revoke_all_credentials"])
def test_l8a_revoke_committing_after_the_preread_wins(cast, live_postgres, monkeypatch, path):
    """The revoke commits between the rotation's first read and its transaction.

    Before LUM-896 the rotation's ``UPDATE`` of the old row matched 0 rows, the
    ``INSERT`` still ran, and the new session outlived the password change.
    """
    import threading

    import auth as auth_mod
    from services.auth_sessions import RefreshError

    ms = live_postgres
    m, m_email = cast.user("member")
    with _app() as client:
        _login(client, m_email)
        cookie = client.cookies.get("lumogis_refresh")
        assert cookie

        paused, release = threading.Event(), threading.Event()
        real_mint = auth_mod.mint_refresh_token

        def mint_then_wait(*a, **k):
            # Called after the pre-read and before ``ms.transaction()``.
            if _in_rotation_thread():
                paused.set()
                assert release.wait(_RACE_WAIT_S), "test never released the rotation"
            return real_mint(*a, **k)

        monkeypatch.setattr(auth_mod, "mint_refresh_token", mint_then_wait)
        out: dict = {}
        t = _rotate_in_thread(m, cookie, out)
        t.start()
        assert paused.wait(_RACE_WAIT_S), "rotation never reached the window"

        _revoke(path, m)
        release.set()
        t.join(_RACE_WAIT_S)
        assert not t.is_alive()

        assert isinstance(out.get("error"), RefreshError), out
        assert _live_sessions(ms, m) == 0
        monkeypatch.setattr(auth_mod, "mint_refresh_token", real_mint)
        assert client.post("/api/v1/auth/refresh").status_code == 401


@pytest.mark.parametrize("path", ["change_own_password", "revoke_all_credentials"])
def test_l8b_revoke_starting_inside_the_rotation_transaction_wins(
    cast, live_postgres, monkeypatch, path
):
    """The revoke starts while the rotation transaction is open, after its INSERT.

    Before LUM-896 the revoke could not see the uncommitted row and left it
    alive. Now the rotation's ``FOR SHARE`` on ``users`` makes the revoke wait
    for its commit, and the revoke then revokes the new row too.
    """
    import threading

    ms = live_postgres
    m, m_email = cast.user("member")
    with _app() as client:
        _login(client, m_email)
        cookie = client.cookies.get("lumogis_refresh")
        assert cookie

        inserted, release = threading.Event(), threading.Event()
        real_execute = ms.execute

        def execute_then_wait(query, params=None):
            real_execute(query, params)
            if _in_rotation_thread() and query.startswith("INSERT INTO auth_sessions"):
                inserted.set()
                assert release.wait(_RACE_WAIT_S), "test never released the rotation"

        monkeypatch.setattr(ms, "execute", execute_then_wait)
        out: dict = {}
        t = _rotate_in_thread(m, cookie, out)
        t.start()
        assert inserted.wait(_RACE_WAIT_S), "rotation never inserted"

        revoke_out: dict = {}

        def run_revoke():
            try:
                _revoke(path, m)
            except Exception as exc:  # noqa: BLE001
                revoke_out["error"] = exc

        r = threading.Thread(target=run_revoke, daemon=True)
        r.start()
        # The revoke must queue behind the rotation's FOR SHARE on ``users``.
        assert _wait_for_lock_waiter(ms), "the revoke never waited on the rotation's lock"
        release.set()
        t.join(_RACE_WAIT_S)
        r.join(_RACE_WAIT_S)
        assert not t.is_alive() and not r.is_alive()
        assert "error" not in revoke_out, revoke_out
        assert "error" not in out, out  # the rotation committed first

        assert _live_sessions(ms, m) == 0, "the rotated-in session survived the revoke"
        monkeypatch.setattr(ms, "execute", real_execute)
        _new_sid, new_refresh, _tv = out["result"]
        assert client.post("/api/v1/auth/refresh").status_code == 401  # the presented cookie
        client.cookies.clear()
        again = client.post("/api/v1/auth/refresh", cookies={"lumogis_refresh": new_refresh})
        assert again.status_code == 401  # nor the rotated-in one
