# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Route tests for the public forgot-password surface (LUM-65).

The central one is
:func:`test_request_latency_parity_when_relay_is_hung` — it points the system
mailer at a relay that never answers and asserts the known and unknown paths
still finish in the same neighbourhood. That test fails under a design that
sends SMTP on the request path and passes under the outbox, which is the whole
reason the outbox exists.
"""

from __future__ import annotations

import base64
import json
import time
import uuid
from contextlib import contextmanager
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from unittest import mock

import pytest
from fastapi.testclient import TestClient
from tests.test_password_reset_service import _ResetFakeStore

import config
from services import altcha_challenge
from services import users as users_svc

_TEST_HMAC = "route-test-altcha-hmac-key-32-chars"
_TEST_FERNET_KEY = "OlGLYckGIbBSt54y8XVmgb441LgKJWvvYoHnpQ_cv9A="


class _ResetRoutesStore(_ResetFakeStore):
    """Reset-token store plus the outbox and system-credential statements."""

    def __init__(self) -> None:
        super().__init__()
        self.outbox: dict[str, dict] = {}
        self.system_credentials: dict[str, dict] = {}

    def execute(self, query: str, params: tuple | None = None) -> None:
        q = self._norm(query)
        p = params or ()
        if q.startswith("insert into system_mail_outbox"):
            row_id, to_addr, subject, body = p
            self.outbox[row_id] = {
                "id": row_id,
                "to_addr": to_addr,
                "subject": subject,
                "body": body,
                "created_at": self._now(),
                "next_attempt_at": self._now(),
                "attempts": 0,
                "sent_at": None,
                "last_error": None,
            }
            return
        if q.startswith("delete from system_mail_outbox where id"):
            self.outbox.pop(p[0], None)
            return
        if q.startswith("update system_mail_outbox set next_attempt_at"):
            minutes, error, row_id = p
            row = self.outbox.get(row_id)
            if row:
                row["last_error"] = error
            return
        return super().execute(query, params)

    def fetch_all(self, query: str, params: tuple | None = None) -> list[dict]:
        q = self._norm(query)
        if q.startswith("update system_mail_outbox set attempts"):
            claimed = []
            for row in self.outbox.values():
                if row["sent_at"] is None and row["next_attempt_at"] <= self._now():
                    row["attempts"] += 1
                    claimed.append(
                        {
                            "id": row["id"],
                            "to_addr": row["to_addr"],
                            "subject": row["subject"],
                            "body": row["body"],
                            "attempts": row["attempts"],
                            # Mirrors the real RETURNING clause. Omitting it made
                            # `_expired()` see None on every row and take the
                            # unknown-age branch, so the expiry cut-off could
                            # never fire under test (LUM-65 P1 #7).
                            "created_at": row["created_at"],
                        }
                    )
            return claimed
        if q.startswith("delete from system_mail_outbox where sent_at is null"):
            # Model the age predicate. This used to return [] unconditionally,
            # so `purge_expired` reported 0 no matter what the outbox held and
            # any test asserting "nothing was purged" passed without exercising
            # a line of the filter.
            ttl_minutes = int((params or (30,))[0])
            cutoff = self._now() - timedelta(minutes=ttl_minutes)
            doomed = [
                r["id"]
                for r in self.outbox.values()
                if r["sent_at"] is None and r["created_at"] < cutoff
            ]
            for row_id in doomed:
                self.outbox.pop(row_id, None)
            return [{"id": row_id} for row_id in doomed]
        return super().fetch_all(query, params)

    def fetch_one(self, query: str, params: tuple | None = None) -> dict | None:
        q = self._norm(query)
        p = params or ()
        if q.startswith("insert into instance_system_connector_credentials"):
            connector, ciphertext, key_version, created_by, updated_by = p
            now = self._now()
            existing = self.system_credentials.get(connector)
            self.system_credentials[connector] = {
                "connector": connector,
                "ciphertext": ciphertext,
                "key_version": key_version,
                "created_at": existing["created_at"] if existing else now,
                "updated_at": now,
                "created_by": existing["created_by"] if existing else created_by,
                "updated_by": updated_by,
            }
            return dict(self.system_credentials[connector])
        if q.startswith("select ciphertext from instance_system_connector_credentials"):
            row = self.system_credentials.get(p[0])
            return {"ciphertext": row["ciphertext"]} if row else None
        if q.startswith("delete from instance_system_connector_credentials"):
            row = self.system_credentials.pop(p[0], None)
            return {"key_version": row["key_version"]} if row else None
        if q.startswith("select") and "instance_system_connector_credentials" in q:
            row = self.system_credentials.get(p[0]) if p else None
            return dict(row) if row else None
        return super().fetch_one(query, params)


class _CapturingSender:
    """Stand-in for the SMTP adapter at the port boundary."""

    def __init__(self) -> None:
        self.sent: list[dict] = []

    def send(self, **kwargs) -> None:
        self.sent.append(kwargs)


class _HungSender:
    """A relay that accepts the connection and then never answers.

    The realistic bad case: not a refused connection (fast failure) but a
    silent one that burns the full submission timeout.
    """

    def __init__(self, delay: float = 2.0) -> None:
        self.delay = delay
        self.calls = 0

    def send(self, **kwargs) -> None:
        self.calls += 1
        time.sleep(self.delay)
        raise TimeoutError("relay never answered")


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> _ResetRoutesStore:
    from services import _credential_internals as ci

    s = _ResetRoutesStore()
    config._instances["metadata_store"] = s
    ci.reset_for_tests()
    yield s
    config._instances.pop("metadata_store", None)
    config._instances.pop("system_smtp_sender", None)
    ci.reset_for_tests()


@pytest.fixture
def reset_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTH_SECRET", "password-reset-route-test-secret")
    monkeypatch.setenv("LUMOGIS_JWT_REFRESH_SECRET", "password-reset-route-refresh")
    monkeypatch.setenv("LUMOGIS_PUBLIC_ORIGIN", "https://home.example.com")
    monkeypatch.setenv("LUMOGIS_ALTCHA_HMAC_KEY", _TEST_HMAC)
    monkeypatch.setenv("LUMOGIS_ALTCHA_COST", "150")
    monkeypatch.setenv("LUMOGIS_CREDENTIAL_KEY", _TEST_FERNET_KEY)
    monkeypatch.delenv("LUMOGIS_CREDENTIAL_KEYS", raising=False)
    altcha_challenge.reset_for_tests()
    from routes.altcha import _reset_limiters_for_tests as _reset_altcha
    from routes.password_reset import _reset_limiters_for_tests

    _reset_limiters_for_tests()
    _reset_altcha()
    yield
    _reset_limiters_for_tests()
    _reset_altcha()
    altcha_challenge.reset_for_tests()


@contextmanager
def _client():
    import main

    with TestClient(main.app) as client:
        yield client


def _seed_user(store: _ResetRoutesStore, email: str, *, disabled: bool = False) -> str:
    user_id = uuid.uuid4().hex
    store.rows[user_id] = {
        "id": user_id,
        "email": email,
        "password_hash": users_svc.hash_password("original-password-1"),
        "role": "user",
        "disabled": disabled,
        "created_at": datetime.now(timezone.utc),
        "last_login_at": None,
        "token_version": 1,
        "allows_shared": True,
    }
    return user_id


def _configure_system_mail(store: _ResetRoutesStore, *, tested: bool = True) -> None:
    from connectors.registry import SYSTEM_SMTP

    from services import credential_tiers as cts

    payload = {
        "host": "smtp.example.com",
        "port": 587,
        "username": "lumogis@example.com",
        "password": "relay-secret",
        "from_addr": "lumogis@example.com",
    }
    if tested:
        payload["last_test_ok_at"] = datetime.now(timezone.utc).isoformat()
    cts.system_put_payload(SYSTEM_SMTP, payload, actor="admin:seed")


def _solved_altcha(client) -> str:
    import altcha

    resp = client.get("/api/v1/altcha/challenge")
    assert resp.status_code == 200, resp.text
    challenge_dict = resp.json()
    challenge = altcha.Challenge.from_dict(challenge_dict)
    solution = altcha.solve_challenge(challenge)
    assert solution is not None
    payload = {"challenge": challenge.to_dict(), "solution": solution.to_dict()}
    return base64.b64encode(json.dumps(payload).encode()).decode()


# ---------------------------------------------------------------------------
# Status + enablement
# ---------------------------------------------------------------------------


def test_status_when_send_test_never_passed_then_disabled(store, reset_env) -> None:
    _configure_system_mail(store, tested=False)

    with _client() as client:
        resp = client.get("/api/v1/password-reset/status")

    assert resp.status_code == 200, resp.text
    assert resp.json() == {"email_reset_enabled": False}


def test_status_when_fully_configured_then_enabled(store, reset_env) -> None:
    _configure_system_mail(store)

    with _client() as client:
        resp = client.get("/api/v1/password-reset/status")

    assert resp.json() == {"email_reset_enabled": True}


@pytest.mark.parametrize("missing", ["origin", "hmac"])
def test_status_when_a_precondition_is_missing_then_disabled(
    store, reset_env, monkeypatch: pytest.MonkeyPatch, missing: str
) -> None:
    _configure_system_mail(store)
    monkeypatch.delenv(
        "LUMOGIS_PUBLIC_ORIGIN" if missing == "origin" else "LUMOGIS_ALTCHA_HMAC_KEY",
        raising=False,
    )

    with _client() as client:
        resp = client.get("/api/v1/password-reset/status")

    assert resp.json() == {"email_reset_enabled": False}


# ---------------------------------------------------------------------------
# Request
# ---------------------------------------------------------------------------


def test_request_when_disabled_then_503(store, reset_env) -> None:
    _configure_system_mail(store, tested=False)
    _seed_user(store, "alice@example.com")

    with _client() as client:
        resp = client.post(
            "/api/v1/password-reset/request",
            json={"email": "alice@example.com", "altcha": "irrelevant"},
        )

    assert resp.status_code == 503, resp.text
    assert resp.json()["detail"]["code"] == "email_reset_disabled"


def test_request_when_known_email_then_mints_and_enqueues_without_smtp(store, reset_env) -> None:
    """The request path must not touch the network — only the drain does."""
    _configure_system_mail(store)
    _seed_user(store, "alice@example.com")
    sender = _CapturingSender()
    config._instances["system_smtp_sender"] = sender

    with _client() as client:
        resp = client.post(
            "/api/v1/password-reset/request",
            json={"email": "alice@example.com", "altcha": _solved_altcha(client)},
        )

    assert resp.status_code == 200, resp.text
    assert resp.json() == {"ok": True}
    assert len(store.reset_tokens) == 1
    assert len(store.outbox) == 1
    assert sender.sent == [], "no SMTP may happen on the request path"

    body = next(iter(store.outbox.values()))["body"]
    assert "https://home.example.com/reset-password?token=prst_" in body


def test_request_when_unknown_email_then_same_response_and_no_mail(store, reset_env) -> None:
    _configure_system_mail(store)
    _seed_user(store, "alice@example.com")

    with _client() as client:
        resp = client.post(
            "/api/v1/password-reset/request",
            json={"email": "nobody@example.com", "altcha": _solved_altcha(client)},
        )

    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    assert store.reset_tokens == {}
    assert store.outbox == {}


def test_request_when_user_disabled_then_treated_as_unknown(store, reset_env) -> None:
    _configure_system_mail(store)
    _seed_user(store, "gone@example.com", disabled=True)

    with _client() as client:
        resp = client.post(
            "/api/v1/password-reset/request",
            json={"email": "gone@example.com", "altcha": _solved_altcha(client)},
        )

    assert resp.status_code == 200
    assert store.outbox == {}


def test_request_when_altcha_missing_or_wrong_then_400_before_any_lookup(store, reset_env) -> None:
    _configure_system_mail(store)
    _seed_user(store, "alice@example.com")

    with _client() as client:
        resp = client.post(
            "/api/v1/password-reset/request",
            json={"email": "alice@example.com", "altcha": "not-a-solution"},
        )

    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"]["code"] == "altcha_invalid"
    assert store.reset_tokens == {}


def test_request_when_altcha_replayed_then_rejected(store, reset_env) -> None:
    _configure_system_mail(store)
    _seed_user(store, "alice@example.com")

    with _client() as client:
        solution = _solved_altcha(client)
        first = client.post(
            "/api/v1/password-reset/request",
            json={"email": "alice@example.com", "altcha": solution},
        )
        second = client.post(
            "/api/v1/password-reset/request",
            json={"email": "alice@example.com", "altcha": solution},
        )

    assert first.status_code == 200
    assert second.status_code == 400, "one proof of work must buy exactly one request"


def test_request_success_path_is_rate_limited(store, reset_env) -> None:
    """Always-200 means a failure-only limiter would never fire."""
    _configure_system_mail(store)
    for i in range(12):
        _seed_user(store, f"user{i}@example.com")

    codes = []
    with _client() as client:
        for i in range(12):
            codes.append(
                client.post(
                    "/api/v1/password-reset/request",
                    json={
                        "email": f"user{i}@example.com",
                        "altcha": _solved_altcha(client),
                    },
                ).status_code
            )

    assert codes.count(200) == 10, codes
    assert codes[-1] == 429, codes


def test_request_latency_parity_when_relay_is_hung(store, reset_env) -> None:
    """The finding this design change exists for.

    With a synchronous send, the known-address branch would wait on the relay
    while the unknown branch returned after the timing floor — an enumeration
    oracle that is loudest exactly when the operator's relay is sick. With the
    outbox, neither branch touches SMTP.
    """
    _configure_system_mail(store)
    _seed_user(store, "alice@example.com")
    hung = _HungSender(delay=2.0)
    config._instances["system_smtp_sender"] = hung

    with _client() as client:
        known_start = time.monotonic()
        known = client.post(
            "/api/v1/password-reset/request",
            json={"email": "alice@example.com", "altcha": _solved_altcha(client)},
        )
        known_elapsed = time.monotonic() - known_start

        unknown_start = time.monotonic()
        unknown = client.post(
            "/api/v1/password-reset/request",
            json={"email": "nobody@example.com", "altcha": _solved_altcha(client)},
        )
        unknown_elapsed = time.monotonic() - unknown_start

    assert known.status_code == unknown.status_code == 200
    assert known.json() == unknown.json()
    assert hung.calls == 0, "the request path must never call the relay"
    # Generous bound: this is about orders of magnitude, not microbenchmarks.
    # A synchronous send against this relay would put ~2s between the branches.
    assert abs(known_elapsed - unknown_elapsed) < 0.35, (
        f"timing oracle: known={known_elapsed:.3f}s unknown={unknown_elapsed:.3f}s"
    )
    # Both branches pay the floor: the known one after mint + enqueue, the
    # unknown one after a _DUMMY_HASH-class argon2 verify. Asserting only the
    # known side would pass even if the unknown branch returned immediately.
    assert known_elapsed >= 0.4, "the wall-clock floor must actually be paid"
    assert unknown_elapsed >= 0.4, "the unknown-address branch must pay it too"


# ---------------------------------------------------------------------------
# Redeem
# ---------------------------------------------------------------------------


def _mint_via_request(client, store, email: str) -> str:
    resp = client.post(
        "/api/v1/password-reset/request",
        json={"email": email, "altcha": _solved_altcha(client)},
    )
    assert resp.status_code == 200, resp.text
    body = next(iter(store.outbox.values()))["body"]
    token = body.split("token=")[1].split()[0].strip()
    return token


def test_redeem_when_token_valid_then_password_changes(store, reset_env) -> None:
    _configure_system_mail(store)
    user_id = _seed_user(store, "alice@example.com")

    with _client() as client:
        token = _mint_via_request(client, store, "alice@example.com")
        resp = client.post(
            "/api/v1/password-reset/redeem",
            json={"token": token, "new_password": "a-brand-new-password"},
        )

    assert resp.status_code == 200, resp.text
    assert resp.json() == {"ok": True}
    assert store.rows[user_id]["token_version"] == 2


def test_redeem_when_replayed_then_404(store, reset_env) -> None:
    _configure_system_mail(store)
    _seed_user(store, "alice@example.com")

    with _client() as client:
        token = _mint_via_request(client, store, "alice@example.com")
        first = client.post(
            "/api/v1/password-reset/redeem",
            json={"token": token, "new_password": "a-brand-new-password"},
        )
        second = client.post(
            "/api/v1/password-reset/redeem",
            json={"token": token, "new_password": "yet-another-password"},
        )

    assert first.status_code == 200
    assert second.status_code == 404, second.text


def test_redeem_when_password_too_short_then_422(store, reset_env) -> None:
    """422 mirrors the invite redeem precedent, this endpoint's public twin."""
    _configure_system_mail(store)
    _seed_user(store, "alice@example.com")

    with _client() as client:
        token = _mint_via_request(client, store, "alice@example.com")
        resp = client.post(
            "/api/v1/password-reset/redeem",
            json={"token": token, "new_password": "short"},
        )

    assert resp.status_code == 422, resp.text


def test_redeem_when_token_unknown_then_404(store, reset_env) -> None:
    _configure_system_mail(store)

    with _client() as client:
        resp = client.post(
            "/api/v1/password-reset/redeem",
            json={"token": "prst_" + "z" * 45, "new_password": "a-brand-new-password"},
        )

    assert resp.status_code == 404, resp.text


def test_redeem_survives_an_smtp_change_that_clears_the_send_test(store, reset_env) -> None:
    """A link already in someone's inbox outlives routine relay maintenance.

    **This inverts an earlier assertion, deliberately** (LUM-65 P1 #5). The
    previous test required a 503 here, on the reasoning that an admin turning
    the feature off should kill links in flight. But there is no "turn the
    feature off" switch — enablement is derived, and clearing the Send test is
    what *rotating the relay password* does. So the rule punished maintenance:
    every outstanding link died, and its holder saw the same "invalid or
    expired" page as a forged token.

    Redeeming needs no SMTP, no public origin and no ALTCHA key — only a hash
    comparison and a password write. An admin who genuinely wants to kill live
    links resets the account password, which invalidates `prst_` tokens through
    `_apply_new_password` on every channel.
    """
    _configure_system_mail(store)
    user_id = _seed_user(store, "alice@example.com")

    with _client() as client:
        token = _mint_via_request(client, store, "alice@example.com")
        _configure_system_mail(store, tested=False)  # e.g. relay password rotated
        resp = client.post(
            "/api/v1/password-reset/redeem",
            json={"token": token, "new_password": "a-brand-new-password"},
        )

    assert resp.status_code == 200, resp.text
    assert store.rows[user_id]["token_version"] == 2, "the password really changed"


def test_redeem_when_auth_disabled_then_503(store, reset_env) -> None:
    """The one conjunct redeem does depend on: no accounts, nothing to recover."""
    _configure_system_mail(store)
    _seed_user(store, "alice@example.com")

    with _client() as client:
        token = _mint_via_request(client, store, "alice@example.com")
        with mock.patch("routes.password_reset.auth_enabled", return_value=False):
            resp = client.post(
                "/api/v1/password-reset/redeem",
                json={"token": token, "new_password": "a-brand-new-password"},
            )

    assert resp.status_code == 503, resp.text


# ---------------------------------------------------------------------------
# Altcha challenge endpoint
# ---------------------------------------------------------------------------


def test_altcha_challenge_when_hmac_unset_then_503(
    store, reset_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("LUMOGIS_ALTCHA_HMAC_KEY", raising=False)

    with _client() as client:
        resp = client.get("/api/v1/altcha/challenge")

    assert resp.status_code == 503, resp.text
    assert resp.json()["detail"]["code"] == "altcha_not_configured"


def test_public_routes_reachable_without_auth(store, reset_env) -> None:
    """All three are on the auth bypass list — a locked-out user has no token."""
    _configure_system_mail(store)

    with _client() as client:
        assert client.get("/api/v1/password-reset/status").status_code == 200
        assert client.get("/api/v1/altcha/challenge").status_code == 200
        assert (
            client.post(
                "/api/v1/password-reset/redeem",
                json={"token": "prst_nope", "new_password": "a-brand-new-password"},
            ).status_code
            == 404
        )
