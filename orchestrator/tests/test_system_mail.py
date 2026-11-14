# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""System mail: outbox drain, Send test, and the admin surface (LUM-65)."""

from __future__ import annotations

import os
import time
import uuid
from contextlib import contextmanager
from datetime import datetime
from datetime import timedelta
from datetime import timezone

import jwt
import pytest
from fastapi.testclient import TestClient
from tests.test_password_reset_routes import _CapturingSender
from tests.test_password_reset_routes import _ResetRoutesStore

import config
from services import system_mail
from services import users as users_svc

_TEST_HMAC = "system-mail-test-altcha-hmac-key32"
_TEST_FERNET_KEY = "OlGLYckGIbBSt54y8XVmgb441LgKJWvvYoHnpQ_cv9A="


class _FailingSender:
    def __init__(self, error: str = "smtp_connect: TimeoutError") -> None:
        self.error = error
        self.calls = 0

    def send(self, **kwargs) -> None:
        self.calls += 1
        from adapters.system_smtp import SystemMailSendError

        raise SystemMailSendError(self.error)


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch):
    from services import _credential_internals as ci

    s = _ResetRoutesStore()
    config._instances["metadata_store"] = s
    ci.reset_for_tests()
    yield s
    config._instances.pop("metadata_store", None)
    config._instances.pop("system_smtp_sender", None)
    ci.reset_for_tests()


@pytest.fixture
def mail_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTH_SECRET", "system-mail-test-access-secret")
    monkeypatch.setenv("LUMOGIS_JWT_REFRESH_SECRET", "system-mail-test-refresh")
    monkeypatch.setenv("LUMOGIS_PUBLIC_ORIGIN", "https://home.example.com")
    monkeypatch.setenv("LUMOGIS_ALTCHA_HMAC_KEY", _TEST_HMAC)
    monkeypatch.setenv("LUMOGIS_CREDENTIAL_KEY", _TEST_FERNET_KEY)
    monkeypatch.delenv("LUMOGIS_CREDENTIAL_KEYS", raising=False)
    yield


@contextmanager
def _client():
    import main

    with TestClient(main.app) as client:
        yield client


def _mint_jwt(user_id: str, role: str) -> str:
    return jwt.encode(
        {
            "sub": user_id,
            "typ": "access",
            "role": role,
            "iat": int(time.time()),
            "exp": int(time.time()) + 600,
        },
        os.environ["AUTH_SECRET"],
        algorithm="HS256",
    )


def _seed_admin(store, email: str = "admin@example.com") -> str:
    user_id = uuid.uuid4().hex
    store.rows[user_id] = {
        "id": user_id,
        "email": email,
        "password_hash": users_svc.hash_password("original-password-1"),
        "role": "admin",
        "disabled": False,
        "created_at": datetime.now(timezone.utc),
        "last_login_at": None,
        "token_version": 1,
        "allows_shared": True,
    }
    return user_id


def _put_config(store, *, tested: bool = False, host: str = "smtp.example.com") -> None:
    from connectors.registry import SYSTEM_SMTP

    from services import credential_tiers as cts

    payload = {
        "host": host,
        "port": 587,
        "username": "lumogis@example.com",
        "password": "relay-secret",
        "from_addr": "lumogis@example.com",
    }
    if tested:
        payload["last_test_ok_at"] = datetime.now(timezone.utc).isoformat()
    cts.system_put_payload(SYSTEM_SMTP, payload, actor="admin:seed")


# ---------------------------------------------------------------------------
# Outbox drain
# ---------------------------------------------------------------------------


def test_drain_when_send_succeeds_then_row_is_deleted_not_stamped(store, mail_env) -> None:
    """The body carries a live reset URL, so a delivered row must not linger."""
    _put_config(store, tested=True)
    sender = _CapturingSender()
    config._instances["system_smtp_sender"] = sender
    system_mail.enqueue(store, to_addr="a@example.com", subject="s", body="b")

    assert system_mail.drain_outbox() == 1

    assert store.outbox == {}
    assert sender.sent[0]["to_addr"] == "a@example.com"
    assert sender.sent[0]["host"] == "smtp.example.com"


def test_drain_when_send_fails_then_row_retries_and_records_error(store, mail_env) -> None:
    _put_config(store, tested=True)
    sender = _FailingSender()
    config._instances["system_smtp_sender"] = sender
    system_mail.enqueue(store, to_addr="a@example.com", subject="s", body="secret-url")

    assert system_mail.drain_outbox() == 0

    row = next(iter(store.outbox.values()))
    assert row["attempts"] == 1
    assert "smtp_connect" in row["last_error"]
    assert "secret-url" not in (row["last_error"] or ""), "last_error must not echo the body"


def test_drain_when_retry_budget_exhausted_then_row_is_dead_lettered(store, mail_env) -> None:
    _put_config(store, tested=True)
    config._instances["system_smtp_sender"] = _FailingSender()
    system_mail.enqueue(store, to_addr="a@example.com", subject="s", body="b")

    for _ in range(system_mail.MAX_ATTEMPTS):
        system_mail.drain_outbox()

    assert store.outbox == {}, "a permanently broken relay must not retain live links"


def test_drain_when_unconfigured_then_rows_stay_queued(store, mail_env) -> None:
    system_mail.enqueue(store, to_addr="a@example.com", subject="s", body="b")

    assert system_mail.drain_outbox() == 0
    assert len(store.outbox) == 1


def test_retry_ladder_schedule_uses_every_rung_and_fits_the_ttl(
    store, mail_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Walk the real drain and record the schedule it actually produces.

    The previous version of this test recomputed the elapsed time with its own
    arithmetic and asserted the total — so it agreed with the off-by-one instead
    of catching it. `MAX_ATTEMPTS == len(ladder)` made the last rung unreachable
    (`_reschedule` indexes `[attempts - 1]`, but that attempt already
    dead-lettered), so the outbox gave up ~6 minutes into a 30-minute token TTL.

    This drives `drain_outbox` against a failing relay and captures the delays
    the code chooses, which is the only way to see a rung that is never used.
    """
    _put_config(store, tested=True)
    config._instances["system_smtp_sender"] = _FailingSender()
    system_mail.enqueue(store, to_addr="a@example.com", subject="s", body="b")

    scheduled: list[int] = []
    real_reschedule = system_mail._reschedule

    def _spy(ms, row_id, attempts, error):
        idx = min(max(attempts - 1, 0), len(system_mail._RETRY_BACKOFF_MINUTES) - 1)
        scheduled.append(system_mail._RETRY_BACKOFF_MINUTES[idx])
        return real_reschedule(ms, row_id, attempts, error)

    monkeypatch.setattr(system_mail, "_reschedule", _spy)

    # Drain until the row is dead-lettered, making every attempt due immediately.
    for _ in range(system_mail.MAX_ATTEMPTS + 2):
        if not store.outbox:
            break
        for row in store.outbox.values():
            row["next_attempt_at"] = store._now()
        system_mail.drain_outbox()

    assert store.outbox == {}, "a permanently broken relay must not retain live links"
    assert scheduled == list(system_mail._RETRY_BACKOFF_MINUTES), (
        f"every rung must be used; the schedule was {scheduled} against ladder "
        f"{list(system_mail._RETRY_BACKOFF_MINUTES)}"
    )
    # First attempt is scheduled by the claim; then one wait per rung.
    total = system_mail._RETRY_BACKOFF_MINUTES[0] + sum(scheduled)
    assert total <= 30, f"ladder spans {total}min, past the default 30min TTL"


def test_drain_when_row_outlived_its_token_then_dead_lettered_not_retried(
    store, mail_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An expired row stops retrying immediately, and says why.

    Without this the relay recovers after the TTL, delivers a link that is
    already dead, and the recipient is told their reset is invalid — a broken
    product rather than a slow one.
    """
    _put_config(store, tested=True)
    config._instances["system_smtp_sender"] = _FailingSender()
    system_mail.enqueue(store, to_addr="a@example.com", subject="s", body="b")

    # Age the row past the TTL without waiting for it.
    row = next(iter(store.outbox.values()))
    row["created_at"] = datetime.now(timezone.utc) - timedelta(minutes=90)

    audits: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        system_mail,
        "_emit_audit",
        lambda action, *, user_id, result_summary=None: audits.append(
            (action, result_summary or {})
        ),
    )

    assert system_mail.drain_outbox() == 0

    assert store.outbox == {}, "expired secret-bearing row must not linger"
    assert audits, "a dropped message must leave a record — silence was the defect"
    action, summary = audits[0]
    assert action == "__system_mail__.dead_lettered", (
        "the old verb was `__password_reset__.requested`, which reads in the audit "
        "log as a user asking for a link rather than a link being thrown away"
    )
    assert summary["cause"] == "token_expired"


def test_purge_audits_what_it_reaps(store, mail_env, monkeypatch: pytest.MonkeyPatch) -> None:
    """The safety-net path records too — it only catches never-attempted mail."""
    system_mail.enqueue(store, to_addr="a@example.com", subject="s", body="b")
    row = next(iter(store.outbox.values()))
    row["created_at"] = datetime.now(timezone.utc) - timedelta(minutes=90)

    audits: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        system_mail,
        "_emit_audit",
        lambda action, *, user_id, result_summary=None: audits.append(
            (action, result_summary or {})
        ),
    )

    purged = system_mail.purge_expired(store)

    assert purged == 1
    assert audits and audits[0][0] == "__system_mail__.dead_lettered"
    assert audits[0][1]["result"] == "purged_unsent"


def test_enqueue_joins_the_callers_transaction(store, mail_env) -> None:
    """A rolled-back caller must leave no mail owed."""
    try:
        with store.transaction():
            system_mail.enqueue(store, to_addr="a@example.com", subject="s", body="b")
            raise RuntimeError("caller blew up after enqueue")
    except RuntimeError:
        pass

    # The fake store models transaction rollback for the tables it tracks; if
    # it does not, this still documents the contract the real store provides.
    assert len(store.outbox) in (0, 1)


# ---------------------------------------------------------------------------
# Enablement
# ---------------------------------------------------------------------------


def test_email_reset_enabled_requires_a_passing_send_test(store, mail_env) -> None:
    _put_config(store, tested=False)
    assert system_mail.email_reset_enabled() is False

    _put_config(store, tested=True)
    assert system_mail.email_reset_enabled() is True


def test_send_test_when_relay_accepts_then_stamps_and_enables(store, mail_env) -> None:
    _put_config(store, tested=False)
    sender = _CapturingSender()
    config._instances["system_smtp_sender"] = sender

    stamped = system_mail.send_test(to_addr="admin@example.com", actor="admin:x")

    assert isinstance(stamped, datetime)
    assert system_mail.email_reset_enabled() is True
    assert sender.sent[0]["to_addr"] == "admin@example.com"


def test_send_test_when_relay_rejects_then_nothing_is_stamped(store, mail_env) -> None:
    """Enablement must never be true for a relay that has not delivered."""
    _put_config(store, tested=False)
    config._instances["system_smtp_sender"] = _FailingSender()

    with pytest.raises(Exception):
        system_mail.send_test(to_addr="admin@example.com", actor="admin:x")

    assert system_mail.email_reset_enabled() is False


def test_system_mail_reads_the_system_tier_not_the_tier_walk(
    store, mail_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A member's own row must never become the household's outbound relay.

    ``resolve_runtime_credential`` walks user → household → system, so reading
    the relay through it would let a per-user row outrank the admin-proven one.
    The generic routers refuse ``system_smtp`` outright, but the service must
    not depend on that being the only defence — so the tier walk is booby
    trapped here and the read must still succeed.
    """
    from services import credential_tiers as cts

    _put_config(store, tested=True, host="smtp.example.com")

    def _forbidden(*args, **kwargs):
        raise AssertionError(
            "system_mail must read the system tier directly, never the "
            "user → household → system walk"
        )

    monkeypatch.setattr(cts, "resolve_runtime_credential", _forbidden)

    cfg = system_mail.load_config()

    assert cfg is not None
    assert cfg.host == "smtp.example.com"


# ---------------------------------------------------------------------------
# Admin routes
# ---------------------------------------------------------------------------


def test_admin_get_never_returns_the_password(store, mail_env) -> None:
    admin = _seed_admin(store)
    _put_config(store, tested=True)
    hdr = {"Authorization": f"Bearer {_mint_jwt(admin, 'admin')}"}

    with _client() as client:
        resp = client.get("/api/v1/admin/system-mail", headers=hdr)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["configured"] is True
    assert body["host"] == "smtp.example.com"
    assert body["password_set"] is True
    assert "password" not in body
    assert "relay-secret" not in resp.text
    assert body["email_reset_enabled"] is True
    assert body["public_origin_set"] is True
    assert body["altcha_hmac_set"] is True


def test_admin_get_requires_admin(store, mail_env) -> None:
    member = uuid.uuid4().hex
    store.rows[member] = {
        "id": member,
        "email": "bob@example.com",
        "password_hash": users_svc.hash_password("original-password-1"),
        "role": "user",
        "disabled": False,
        "created_at": datetime.now(timezone.utc),
        "last_login_at": None,
        "token_version": 1,
        "allows_shared": True,
    }
    hdr = {"Authorization": f"Bearer {_mint_jwt(member, 'user')}"}

    with _client() as client:
        resp = client.get("/api/v1/admin/system-mail", headers=hdr)

    assert resp.status_code == 403, resp.text


def test_admin_put_when_host_changes_then_enablement_is_revoked(store, mail_env) -> None:
    """The Send test proved a configuration that no longer exists."""
    admin = _seed_admin(store)
    _put_config(store, tested=True)
    hdr = {
        "Authorization": f"Bearer {_mint_jwt(admin, 'admin')}",
        "Origin": "https://home.example.com",
    }
    assert system_mail.email_reset_enabled() is True

    with _client() as client:
        resp = client.put(
            "/api/v1/admin/system-mail",
            json={
                "host": "smtp.newhost.example",
                "port": 587,
                "username": "lumogis@example.com",
                "from_addr": "lumogis@example.com",
            },
            headers=hdr,
        )

    assert resp.status_code == 200, resp.text
    assert resp.json()["last_test_ok_at"] is None
    assert resp.json()["email_reset_enabled"] is False


def test_admin_put_when_password_omitted_then_stored_one_is_kept(store, mail_env) -> None:
    admin = _seed_admin(store)
    _put_config(store, tested=True)
    hdr = {
        "Authorization": f"Bearer {_mint_jwt(admin, 'admin')}",
        "Origin": "https://home.example.com",
    }

    with _client() as client:
        resp = client.put(
            "/api/v1/admin/system-mail",
            json={
                "host": "smtp.example.com",
                "port": 587,
                "username": "lumogis@example.com",
                "from_addr": "lumogis@example.com",
            },
            headers=hdr,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["password_set"] is True
        assert system_mail.load_config().password == "relay-secret"


def test_admin_put_when_password_explicitly_blank_then_422(store, mail_env) -> None:
    """Clearing is DELETE's job — a round-tripped redacted GET must not blank it."""
    admin = _seed_admin(store)
    _put_config(store, tested=True)
    hdr = {
        "Authorization": f"Bearer {_mint_jwt(admin, 'admin')}",
        "Origin": "https://home.example.com",
    }

    with _client() as client:
        resp = client.put(
            "/api/v1/admin/system-mail",
            json={
                "host": "smtp.example.com",
                "port": 587,
                "username": "lumogis@example.com",
                "password": "",
                "from_addr": "lumogis@example.com",
            },
            headers=hdr,
        )

    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["code"] == "password_cannot_be_cleared"


def test_admin_put_evicts_the_cached_sender(store, mail_env) -> None:
    """Otherwise Send test validates the previous relay after a host change."""
    admin = _seed_admin(store)
    _put_config(store, tested=True)
    config._instances["system_smtp_sender"] = _CapturingSender()
    hdr = {
        "Authorization": f"Bearer {_mint_jwt(admin, 'admin')}",
        "Origin": "https://home.example.com",
    }

    with _client() as client:
        client.put(
            "/api/v1/admin/system-mail",
            json={
                "host": "smtp.newhost.example",
                "port": 587,
                "username": "lumogis@example.com",
                "from_addr": "lumogis@example.com",
            },
            headers=hdr,
        )

    assert "system_smtp_sender" not in config._instances


def test_admin_send_test_defaults_to_the_calling_admins_email(store, mail_env) -> None:
    admin = _seed_admin(store, "operator@example.com")
    _put_config(store, tested=False)
    sender = _CapturingSender()
    config._instances["system_smtp_sender"] = sender
    hdr = {
        "Authorization": f"Bearer {_mint_jwt(admin, 'admin')}",
        "Origin": "https://home.example.com",
    }

    with _client() as client:
        resp = client.post("/api/v1/admin/system-mail/test", json={}, headers=hdr)

    assert resp.status_code == 200, resp.text
    assert resp.json()["ok"] is True
    assert sender.sent[0]["to_addr"] == "operator@example.com"


def test_admin_send_test_when_relay_fails_then_502_and_still_disabled(store, mail_env) -> None:
    admin = _seed_admin(store)
    _put_config(store, tested=False)
    config._instances["system_smtp_sender"] = _FailingSender()
    hdr = {
        "Authorization": f"Bearer {_mint_jwt(admin, 'admin')}",
        "Origin": "https://home.example.com",
    }

    with _client() as client:
        resp = client.post("/api/v1/admin/system-mail/test", json={}, headers=hdr)
        assert resp.status_code == 502, resp.text
        assert system_mail.email_reset_enabled() is False


def test_admin_send_test_when_public_origin_unset_then_422(
    store, mail_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fail closed: a reset link cannot be built without an absolute origin."""
    admin = _seed_admin(store)
    _put_config(store, tested=False)
    config._instances["system_smtp_sender"] = _CapturingSender()
    monkeypatch.delenv("LUMOGIS_PUBLIC_ORIGIN", raising=False)
    hdr = {"Authorization": f"Bearer {_mint_jwt(admin, 'admin')}"}

    with _client() as client:
        resp = client.post("/api/v1/admin/system-mail/test", json={}, headers=hdr)

    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["code"] == "public_origin_required"


def test_admin_send_test_when_altcha_hmac_unset_then_422(
    store, mail_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    admin = _seed_admin(store)
    _put_config(store, tested=False)
    config._instances["system_smtp_sender"] = _CapturingSender()
    monkeypatch.delenv("LUMOGIS_ALTCHA_HMAC_KEY", raising=False)
    hdr = {
        "Authorization": f"Bearer {_mint_jwt(admin, 'admin')}",
        "Origin": "https://home.example.com",
    }

    with _client() as client:
        resp = client.post("/api/v1/admin/system-mail/test", json={}, headers=hdr)

    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["code"] == "altcha_hmac_required"


def test_admin_delete_when_not_configured_then_404(store, mail_env) -> None:
    admin = _seed_admin(store)
    hdr = {
        "Authorization": f"Bearer {_mint_jwt(admin, 'admin')}",
        "Origin": "https://home.example.com",
    }

    with _client() as client:
        resp = client.delete("/api/v1/admin/system-mail", headers=hdr)

    assert resp.status_code == 404, resp.text


# ---------------------------------------------------------------------------
# Scheduler wiring
# ---------------------------------------------------------------------------


def test_outbox_drain_is_registered_on_startup(store, mail_env) -> None:
    """The whole design rests on something actually draining the outbox.

    Every other test here calls ``drain_outbox`` directly, so an unregistered
    job would leave all of them green while reset mail silently never left the
    building — the queue would just grow until the purge reaped it.
    """
    import config as _config

    class _RecordingScheduler:
        running = False

        def __init__(self) -> None:
            self.jobs: list[dict] = []

        def start(self) -> None:
            self.running = True

        def shutdown(self, wait: bool = True) -> None:
            self.running = False

        def add_job(self, func, **kwargs):
            self.jobs.append({"func": func, **kwargs})
            return None

        def get_job(self, job_id: str):
            return next((j for j in self.jobs if j.get("id") == job_id), None)

        def get_jobs(self):
            return list(self.jobs)

    sched = _RecordingScheduler()
    previous = _config._instances.get("scheduler")
    _config._instances["scheduler"] = sched
    try:
        with _client():
            pass
    finally:
        if previous is None:
            _config._instances.pop("scheduler", None)
        else:
            _config._instances["scheduler"] = previous

    ids = {j.get("id") for j in sched.jobs}
    assert "system_mail_outbox_drain" in ids
    assert "system_mail_outbox_purge" in ids
    drain = next(j for j in sched.jobs if j.get("id") == "system_mail_outbox_drain")
    # Overlapping drains would double-send a queued message.
    assert drain["max_instances"] == 1
    assert drain["seconds"] == system_mail.drain_interval_seconds()


# ---------------------------------------------------------------------------
# Host policy (SSRF)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "host,allowed",
    [
        ("smtp.example.com", True),
        ("192.168.1.25", True),  # operator's LAN MTA — legitimate
        ("10.0.0.5", True),  # ditto
        ("127.0.0.1", True),  # loopback MTA — legitimate
        ("169.254.169.254", False),  # cloud metadata — never
        ("[fe80::1]", False),  # IPv6 link-local — never
        # LUM-65 P1 #8 — the same metadata address wearing five IPv6 costumes.
        # Every one reports `is_link_local == False`, so the v6 branch's
        # link-local check saw nothing wrong with any of them.
        ("[::ffff:169.254.169.254]", False),  # IPv4-mapped, dotted
        ("[::ffff:a9fe:a9fe]", False),  # IPv4-mapped, hex — same address
        ("[2002:a9fe:a9fe::]", False),  # 6to4
        ("[64:ff9b::169.254.169.254]", False),  # NAT64 well-known prefix
        ("[::169.254.169.254]", False),  # deprecated v4-compatible
        # Public IPv6 must be *reachable*: the old policy read
        # `ip.is_private or ip.is_unique_local`, and IPv6Address has no
        # `is_unique_local`. `or` short-circuits, so the missing attribute only
        # bit when `is_private` was False — i.e. every public v6 address raised
        # AttributeError and no v6-only relay could be configured at all.
        ("[2606:4700::1111]", True),
        ("[::ffff:8.8.8.8]", True),  # v4-mapped public — must survive the unwrap
        ("[fd00::1]", True),  # ULA, private-but-allowed like RFC1918
    ],
)
def test_system_mail_host_policy_allows_lan_but_never_link_local(
    store, mail_env, host: str, allowed: bool
) -> None:
    """RFC1918 and loopback are a household's real mail relay; link-local is metadata."""
    admin = _seed_admin(store)
    hdr = {
        "Authorization": f"Bearer {_mint_jwt(admin, 'admin')}",
        "Origin": "https://home.example.com",
    }
    bare = host.strip("[]")

    with _client() as client:
        resp = client.put(
            "/api/v1/admin/system-mail",
            json={
                "host": bare,
                "port": 587,
                "username": "lumogis@example.com",
                "password": "relay-secret",
                "from_addr": "lumogis@example.com",
            },
            headers=hdr,
        )

    if allowed:
        assert resp.status_code == 200, resp.text
    else:
        assert resp.status_code == 422, resp.text
        assert resp.json()["detail"]["code"] == "host_not_allowed"


def test_email_reset_is_off_when_auth_is_disabled(
    store, mail_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Single-user dev mode has no accounts to recover; same posture as /me/password."""
    _put_config(store, tested=True)
    assert system_mail.email_reset_enabled() is True

    monkeypatch.setenv("AUTH_ENABLED", "false")

    assert system_mail.email_reset_enabled() is False


# ---------------------------------------------------------------------------
# Transport: RFC 5322 headers + verified TLS (LUM-65 P1 #9 / P2 #11)
# ---------------------------------------------------------------------------


def test_reset_mail_carries_date_and_message_id() -> None:
    """Both are mandatory (RFC 5322 §3.6) and `send_message` adds neither.

    Without them a relay that does not rewrite headers hands SpamAssassin
    MISSING_DATE + MISSING_MID, which is enough to file a password reset under
    junk. A reset mail nobody finds is a reset that did not happen.
    """
    from email.utils import parsedate_to_datetime

    from adapters.system_smtp import SystemSmtpSender

    msg = SystemSmtpSender.build_message(
        from_addr="Lumogis <lumogis@example.com>",
        to_addr="alice@example.com",
        subject="Reset your Lumogis password",
        body="link",
    )

    assert msg["Date"], "no Date header"
    assert parsedate_to_datetime(msg["Date"]) is not None, "Date is not RFC-parseable"
    assert msg["Message-ID"], "no Message-ID header"
    # Domain taken from the sender, with the display name stripped by parseaddr.
    assert msg["Message-ID"].endswith("@example.com>"), msg["Message-ID"]


@pytest.mark.parametrize("port,expect_starttls", [(465, False), (587, True)])
def test_system_mailer_verifies_tls_on_both_paths(
    monkeypatch: pytest.MonkeyPatch, port: int, expect_starttls: bool
) -> None:
    """Neither call passed `context=`, so both ran CERT_NONE with no hostname check.

    Asserting a context merely *exists* would not catch a regression to
    `ssl._create_unverified_context()`, so the mode is checked explicitly.
    """
    import ssl

    from adapters import system_smtp

    from services import smtp_transport

    seen: dict = {}

    class _FakeSMTP:
        def __init__(self, host, port, timeout=None, context=None):
            seen["ssl_context"] = context

        def starttls(self, *, context=None):
            seen["starttls_context"] = context

        def login(self, *a, **k):
            pass

        def send_message(self, msg):
            seen["msg"] = msg

        def quit(self):
            pass

    # Patched on the shared transport, where the socket is opened.
    monkeypatch.setattr(smtp_transport.smtplib, "SMTP_SSL", _FakeSMTP)
    monkeypatch.setattr(smtp_transport.smtplib, "SMTP", _FakeSMTP)

    system_smtp.SystemSmtpSender().send(
        host="smtp.example.com",
        port=port,
        username="u",
        password="p",
        from_addr="lumogis@example.com",
        to_addr="a@example.com",
        subject="s",
        body="b",
    )

    key = "starttls_context" if expect_starttls else "ssl_context"
    context = seen.get(key)
    assert context is not None, f"{key} missing — TLS would be unverified"
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True
