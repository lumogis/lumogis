# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""services.home_dns.service + worker_client (LUM-331).

The fake Worker implements the real contract, including Ed25519 verification of
the registration signature and a fresh bearer on every ``/register``, because a
fake that accepted anything could not catch a wrong encoding or a stale bearer.
The fake sidecar checks the pending file really exists before "validating" it.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import secrets
import stat
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from email.utils import format_datetime
from pathlib import Path

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from services.home_dns import service
from services.home_dns import state as st
from services.home_dns import worker_client as wc

ZONE = "homes.lumogis.app"


def _b64u_decode(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


class FakeWorker:
    """The Home DNS Worker contract, in memory."""

    def __init__(self) -> None:
        self.households: dict[str, dict] = {}
        self.calls: list[tuple[str, str]] = []
        self.fail: dict[str, httpx.Response] = {}  # path → canned response
        self.budget_available = True
        self.date_header: str | None = None
        self.secret = "public-anti-spam"

    def _slug(self, pubkey: str) -> str:
        digest = hashlib.sha256(pubkey.encode()).digest()
        return base64.b32encode(digest).decode().lower()[:26]

    def _who(self, request: httpx.Request) -> dict | None:
        auth = request.headers.get("Authorization", "")
        token = auth.removeprefix("Bearer ")
        return next((h for h in self.households.values() if h["bearer"] == token), None)

    def handle(self, method: str, url: str, **kw) -> httpx.Response:
        request = httpx.Request(method, url, headers=kw.get("headers"), json=kw.get("json"))
        path = request.url.path
        self.calls.append((method, path))
        headers = {"Date": self.date_header} if self.date_header else {}
        if path in self.fail:
            return self.fail[path]
        if method == "POST" and path == "/register":
            body = kw["json"]
            pub = Ed25519PublicKey.from_public_bytes(_b64u_decode(body["pubkey"]))
            message = f"register|{body['pubkey']}|{body['ts']}".encode()
            pub.verify(_b64u_decode(body["sig"]), message)  # raises on a bad signature
            assert body["registration_secret"] == self.secret
            slug = self._slug(body["pubkey"])
            token = secrets.token_urlsafe(32)  # fresh bearer every time
            h = self.households.setdefault(slug, {"a": None, "txt_first": None})
            h["bearer"] = token
            return httpx.Response(
                201,
                json={"household_id": slug, "name": f"{slug}.{ZONE}", "token": token},
                headers=headers,
            )
        who = self._who(request)
        if who is None:
            return httpx.Response(401, json={"error": "unauthorized"})
        if method == "PUT" and path == "/a":
            who["a"] = kw["json"]["ip"]
            return httpx.Response(200, json={"ip": who["a"]})
        if method == "GET" and path == "/budget":
            return httpx.Response(
                200,
                json={
                    "first_cert_needed": who["txt_first"] is None,
                    "available": self.budget_available,
                    "scope": None if self.budget_available else "global",
                    "retry_after_seconds": None if self.budget_available else 3600,
                },
            )
        if method == "DELETE" and path == "/register":
            who["a"] = None
            who["bearer"] = None
            return httpx.Response(204)
        return httpx.Response(404)


class FakeScheduler:
    """Records ``add_job`` calls; jobs are run by the test, never by a clock."""

    def __init__(self) -> None:
        self.jobs: list[dict] = []

    def add_job(self, func, **kwargs):
        self.jobs.append({"func": func, **kwargs})

    def remove_job(self, job_id):
        self.jobs = [j for j in self.jobs if j.get("id") != job_id]

    def ids(self) -> list[str]:
        return [j.get("id") for j in self.jobs]


class FakeSidecar:
    def __init__(self, caddy_home: Path) -> None:
        self.caddy_home = caddy_home
        self.calls: list[str] = []
        self.detected: dict = {"address": "192.168.1.20", "reason": None}
        self.apply_code: str | None = None
        self.rejected_at_start = False
        self.reload_ok = True
        self.on_apply = None  # called while Caddy "validates" (to interleave an admin action)
        self.apply_raises = False  # /caddy/apply raises (stack-control unreachable)
        self.on_detect = None  # called inside /host-lan-address (to hold it on an Event)
        self.on_reload = None  # called inside /caddy/reload (to interleave an admin action)
        self.reload_raises = False  # /caddy/reload raises (a timeout: the outcome is unknown)
        # /caddy/home-status "present": None = report whether home.caddy exists
        self.present: bool | None = None
        self.home_status_reachable = True
        self.timeouts: dict[str, list[float]] = {}
        self.restart_ok = True  # /caddy/restart (LUM-1004)

    def __call__(self, method: str, path: str, timeout: float) -> dict:
        self.calls.append(path)
        self.timeouts.setdefault(path, []).append(timeout)
        if path == "/host-lan-address":
            if self.on_detect:
                self.on_detect()
            return dict(self.detected)
        if path == "/caddy/apply":
            if self.apply_raises:
                raise RuntimeError("stack-control unreachable")
            if self.apply_code:
                return {"ok": False, "code": self.apply_code}
            assert (self.caddy_home / "home.caddy.pending").is_file()
            if self.on_apply:
                self.on_apply()
            return {"ok": True, "code": "validated"}
        if path == "/caddy/reload":
            if self.on_reload:
                self.on_reload()
            if self.reload_raises:
                raise RuntimeError("timed out")
            return {"ok": self.reload_ok, "code": "reloaded" if self.reload_ok else "reload_failed"}
        if path == "/caddy/home-status":
            if not self.home_status_reachable:
                return {"present": None, "rejected_at_start": None, "reachable": False}
            present = (
                (self.caddy_home / "home.caddy").exists() if self.present is None else self.present
            )
            return {
                "present": present,
                "rejected_at_start": self.rejected_at_start,
                "reachable": True,
            }
        if path == "/caddy/restart":
            code = "restarted" if self.restart_ok else "restart_failed"
            return {"ok": self.restart_ok, "code": code}
        raise AssertionError(path)


@pytest.fixture
def env(tmp_path, monkeypatch):
    caddy_home = tmp_path / "caddy-home"
    caddy_home.mkdir()
    monkeypatch.setenv("WORKSPACE_PATH", str(tmp_path / "ws"))
    monkeypatch.setenv("CADDY_HOME_DIR", str(caddy_home))
    monkeypatch.setenv("LUMOGIS_HOME_DNS_URL", "https://dns.example.test")
    monkeypatch.setenv("LUMOGIS_HOME_DNS_REGISTRATION_SECRET", "public-anti-spam")
    monkeypatch.delenv("LUMOGIS_HOME_DNS_ZONE", raising=False)
    monkeypatch.delenv("LUMOGIS_SECURE_ADDRESS_PORT", raising=False)
    monkeypatch.delenv("LUMOGIS_HOME_DNS_ACME_STAGING", raising=False)
    # Module state that outlives a test: the detect reuse window and the warn-once flag.
    monkeypatch.setattr(service, "_last_detect_done", float("-inf"))
    monkeypatch.setattr(service, "_render_warned", False)
    monkeypatch.setattr(service, "_last_ca_restart", None)
    import config as _config

    sched = FakeScheduler()
    monkeypatch.setattr(_config, "get_scheduler", lambda: sched)
    worker = FakeWorker()
    monkeypatch.setattr(
        wc.httpx, "request", lambda method, url, timeout, **kw: worker.handle(method, url, **kw)
    )
    sidecar = FakeSidecar(caddy_home)
    monkeypatch.setattr(service, "_sidecar", sidecar)
    probe = {"kind": "no_cert"}
    now = datetime.now(timezone.utc)
    monkeypatch.setattr(
        service,
        "probe_certificate",
        lambda name: (
            (probe["kind"], now - timedelta(days=1), now + timedelta(days=89))
            if probe["kind"] in ("ok", "other_ca")
            else (probe["kind"], None, None)
        ),
    )
    monkeypatch.setattr(service, "resolver_check", lambda name, ip, deadline=2.0: "ok")
    return {
        "worker": worker,
        "sidecar": sidecar,
        "caddy_home": caddy_home,
        "probe": probe,
        "sched": sched,
    }


def _enable(address: str = "192.168.1.20") -> int:
    with service.route_lock():
        return service.enable(address, None)


def _snippet(env) -> str:
    return (env["caddy_home"] / "home.caddy").read_text()


# --------------------------------------------------------------------------- enable


def test_enable_happy_path_writes_golden_snippet(env) -> None:
    from services.home_dns.snippet import render

    gen = _enable()
    service._advance(gen)
    s = st.load()
    assert s.registered_at and s.last_a_update_at and s.snippet_applied_at and s.last_error is None
    assert env["worker"].households[s.household_id]["a"] == "192.168.1.20"
    assert _snippet(env) == render(s.household_id, s.bearer, "https://dns.example.test")
    assert not (env["caddy_home"] / "home.caddy.pending").exists()
    assert env["sidecar"].calls[-2:] == ["/caddy/apply", "/caddy/reload"]
    assert stat.S_IMODE((env["caddy_home"] / "home.caddy").stat().st_mode) == 0o640


def test_device_key_is_0600_and_reused(env) -> None:
    key1, created1 = service.load_or_create_device_key()
    key2, created2 = service.load_or_create_device_key()
    assert created1 and not created2
    assert key1.public_key().public_bytes_raw() == key2.public_key().public_bytes_raw()
    path = st.home_dir() / "device_ed25519.pem"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_register_retry_rewrites_snippet_with_new_bearer(env) -> None:
    gen = _enable()
    service._advance(gen)
    first = st.load().bearer
    service._register(gen, st.load())
    service._advance(gen)
    second = st.load().bearer
    assert first != second
    assert second in _snippet(env) and first not in _snippet(env)


def test_budget_exhausted_writes_no_snippet(env) -> None:
    env["worker"].budget_available = False
    gen = _enable()
    service._advance(gen)
    s = st.load()
    assert s.last_error == "budget_exhausted" and s.next_retry_at is not None
    assert "budget_exhausted" in s.warnings
    assert not (env["caddy_home"] / "home.caddy").exists()
    assert "/caddy/apply" not in env["sidecar"].calls


def test_partial_failure_keeps_enabled_and_resumes(env) -> None:
    env["sidecar"].apply_code = "caddy_unreachable"
    gen = _enable()
    service._advance(gen)
    s = st.load()
    assert s.enabled and s.last_error == "caddy_unreachable" and s.snippet_applied_at is None
    assert ("DELETE", "/register") not in env["worker"].calls
    env["sidecar"].apply_code = None
    registrations = env["worker"].calls.count(("POST", "/register"))
    service._advance(gen)
    assert st.load().snippet_applied_at is not None
    assert env["worker"].calls.count(("POST", "/register")) == registrations  # resumed, not redone


def test_401_on_put_a_reregisters(env) -> None:
    gen = _enable()
    service._advance(gen)
    s = st.load()
    env["worker"].households[s.household_id]["bearer"] = "invalidated-by-a-clone"
    service.set_lan_address("192.168.1.21", None)
    service._advance(st.load().generation)
    s2 = st.load()
    assert s2.bearer != s.bearer
    assert env["worker"].households[s2.household_id]["a"] == "192.168.1.21"
    assert s2.bearer in _snippet(env)


@pytest.mark.parametrize(
    "status,error,code",
    [
        (403, "registration_forbidden", "registration_refused"),
        (410, "retired", "registration_retired"),
        (429, "rate_limited", "registration_rate_limited"),
        (502, "upstream", "worker_unreachable"),
    ],
)
def test_register_errors_map(env, status, error, code) -> None:
    env["worker"].fail["/register"] = httpx.Response(status, json={"error": error})
    gen = _enable()
    service._advance(gen)
    assert st.load().last_error == code


def test_clock_skew_detected_from_date_header(env) -> None:
    far = datetime.now(timezone.utc) + timedelta(minutes=10)
    env["worker"].fail["/register"] = httpx.Response(
        400, json={"error": "bad_ts"}, headers={"Date": format_datetime(far, usegmt=True)}
    )
    gen = _enable()
    service._advance(gen)
    assert st.load().last_error == "clock_skew"


def test_unset_registration_secret_uses_the_release_default(env, monkeypatch) -> None:
    monkeypatch.delenv("LUMOGIS_HOME_DNS_REGISTRATION_SECRET")
    env["worker"].secret = wc.DEFAULT_REGISTRATION_SECRET  # the fake asserts what arrives
    service._advance(_enable())
    assert ("POST", "/register") in env["worker"].calls
    assert st.load().registered_at is not None


def test_blank_registration_secret_env_uses_the_release_default(env, monkeypatch) -> None:
    monkeypatch.setenv("LUMOGIS_HOME_DNS_REGISTRATION_SECRET", "   ")
    env["worker"].secret = wc.DEFAULT_REGISTRATION_SECRET
    service._advance(_enable())
    assert st.load().registered_at is not None


def test_registration_secret_env_overrides_the_default(env) -> None:
    service._advance(_enable())  # the fixture's value reaches the Worker (it asserts it)
    assert st.load().registered_at is not None
    assert env["worker"].secret == "public-anti-spam" != wc.DEFAULT_REGISTRATION_SECRET


def test_release_registration_value_shape() -> None:
    """One source for the value: Core's code default (rotation is a Core release)."""
    import re

    root = Path(__file__).resolve().parent.parent.parent
    value = wc.DEFAULT_REGISTRATION_SECRET
    assert re.fullmatch(r"[0-9a-f]{64}", value)
    assert value not in ("public-anti-spam", "test-reg-secret")
    example = (root / ".env.example").read_text(encoding="utf-8")
    assert not re.search(r"^LUMOGIS_HOME_DNS_REGISTRATION_SECRET=", example, flags=re.M)
    release_env = (root / "docker" / "release" / "example.env").read_text(encoding="utf-8")
    assert "LUMOGIS_HOME_DNS_REGISTRATION_SECRET" not in release_env


def test_worker_returning_foreign_name_is_refused(env, monkeypatch) -> None:
    real = env["worker"].handle

    def tampered(method, url, **kw):
        resp = real(method, url, **kw)
        if url.endswith("/register") and resp.status_code == 201:
            body = resp.json()
            body["name"] = "other.example.test"
            return httpx.Response(201, json=body)
        return resp

    monkeypatch.setattr(
        wc.httpx, "request", lambda method, url, timeout, **kw: tampered(method, url, **kw)
    )
    gen = _enable()
    service._advance(gen)
    assert st.load().last_error == "registration_refused"


def test_stack_control_404_maps_outdated(env, monkeypatch) -> None:
    def outdated(method, path, timeout):
        raise service._Outdated()

    gen = _enable()
    monkeypatch.setattr(service, "_sidecar", outdated)
    service._advance(gen)
    assert st.load().last_error == "stack_control_outdated"


def test_reload_failure_removes_snippet_and_reloads(env) -> None:
    env["sidecar"].reload_ok = False
    gen = _enable()
    service._advance(gen)
    assert st.load().last_error == "caddy_reload_failed"
    assert not (env["caddy_home"] / "home.caddy").exists()
    assert env["sidecar"].calls.count("/caddy/reload") == 2


def test_volume_missing_when_caddy_home_absent(env, monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("CADDY_HOME_DIR", str(tmp_path / "nope"))
    gen = _enable()
    service._advance(gen)
    assert st.load().last_error == "caddy_volume_missing"


# --------------------------------------------------------------------------- disable


def test_disable_offline_then_deregisters(env) -> None:
    gen = _enable()
    service._advance(gen)
    hid = st.load().household_id
    env["worker"].fail["/register"] = httpx.Response(503, json={"error": "down"})
    with service.route_lock():
        service.disable()
    service.after_disable()
    s = st.load()
    assert not s.enabled and s.pending_deregister and s.bearer
    assert not (env["caddy_home"] / "home.caddy").exists()
    del env["worker"].fail["/register"]
    service.refresh_job()
    s = st.load()
    assert not s.pending_deregister and s.bearer is None
    assert env["worker"].households[hid]["a"] is None


def test_enable_after_offline_disable_does_not_deregister(env) -> None:
    gen = _enable()
    service._advance(gen)
    env["worker"].fail["/register"] = httpx.Response(503, json={"error": "down"})
    with service.route_lock():
        service.disable()
    service.after_disable()  # its owed DELETE fails: the Worker is down
    del env["worker"].fail["/register"]
    deletes_before = env["worker"].calls.count(("DELETE", "/register"))
    gen2 = _enable()
    assert not st.load().pending_deregister
    service._advance(gen2)
    service.refresh_job()
    assert env["worker"].calls.count(("DELETE", "/register")) == deletes_before
    s = st.load()
    assert s.enabled and env["worker"].households[s.household_id]["a"] == "192.168.1.20"


def test_disable_gives_up_after_7_days(env) -> None:
    gen = _enable()
    service._advance(gen)
    with service.route_lock():
        service.disable()
    s = st.load()
    s.pending_deregister_since = datetime.now(timezone.utc) - timedelta(days=8)
    st.save(s)
    env["worker"].fail["/register"] = httpx.Response(503, json={"error": "down"})
    service.try_deregister()
    assert not st.load().pending_deregister


# --------------------------------------------------------------------------- upkeep


def test_detected_address_follows_the_host(env) -> None:
    gen = _enable()
    service._advance(gen)
    s = st.load()
    s.lan_address_source = "detected"
    st.save(s)
    env["sidecar"].detected = {"address": "192.168.1.33", "reason": None}
    service.refresh_job()
    s = st.load()
    assert s.lan_address == "192.168.1.33"
    assert env["worker"].households[s.household_id]["a"] == "192.168.1.33"


def test_manual_address_not_overwritten_by_detection(env) -> None:
    gen = _enable("192.168.1.50")  # not the detected one → manual
    service._advance(gen)
    assert st.load().lan_address_source == "manual"
    service.refresh_job()
    s = st.load()
    assert s.lan_address == "192.168.1.50"
    assert "address_mismatch" in s.warnings


def test_probe_cache_rules(env) -> None:
    gen = _enable()
    service._advance(gen)
    env["probe"]["kind"] = "ok"
    service.refresh_job()
    assert st.load().cert_not_after is not None
    env["probe"]["kind"] = "unreachable"
    for _ in range(3):
        service.refresh_job()
    s = st.load()
    assert s.cert_not_after is not None  # kept across Caddy restarts
    assert "caddy_unreachable" in s.warnings
    assert service.public_view().url is not None
    env["probe"]["kind"] = "no_cert"
    service.refresh_job()
    assert st.load().cert_not_after is None


def test_fallback_recovery_reapplies(env) -> None:
    gen = _enable()
    service._advance(gen)
    env["sidecar"].rejected_at_start = True
    applies = env["sidecar"].calls.count("/caddy/apply")
    service.refresh_job()
    assert env["sidecar"].calls.count("/caddy/apply") == applies + 1
    assert (env["caddy_home"] / "home.caddy").exists()


def test_fallback_recovery_removes_when_still_rejected(env) -> None:
    gen = _enable()
    service._advance(gen)
    env["sidecar"].rejected_at_start = True
    env["sidecar"].apply_code = "caddy_snippet_rejected"
    service.refresh_job()
    assert not (env["caddy_home"] / "home.caddy").exists()
    assert st.load().last_error == "caddy_snippet_rejected"


def test_daily_heartbeat_detects_dead_bearer(env) -> None:
    gen = _enable()
    service._advance(gen)
    s = st.load()
    s.last_heartbeat_at = datetime.now(timezone.utc) - timedelta(days=2)
    st.save(s)
    env["worker"].households[s.household_id]["bearer"] = "restored-old-backup"
    service.refresh_job()
    s2 = st.load()
    assert s2.bearer != s.bearer and s2.bearer in _snippet(env)


def test_state_corrupt_removes_snippet(env) -> None:
    gen = _enable()
    service._advance(gen)
    st.state_path().write_text("{broken")
    service.refresh_job()
    assert st.load().last_error == "state_corrupt"
    assert not (env["caddy_home"] / "home.caddy").exists()


def test_self_clearing_code_retries_after_upgrade(env) -> None:
    env["sidecar"].apply_code = "caddy_image_outdated"
    gen = _enable()
    service._advance(gen)
    assert st.load().last_error == "caddy_image_outdated"
    env["sidecar"].apply_code = None
    s = st.load()
    s.next_retry_at = None
    st.save(s)
    service.refresh_job()
    assert st.load().snippet_applied_at is not None


def test_retry_clears_errors_and_bumps_generation(env) -> None:
    env["sidecar"].apply_code = "caddy_snippet_rejected"
    gen = _enable()
    service._advance(gen)
    env["sidecar"].apply_code = None
    with service.route_lock():
        gen2 = service.retry()
    assert gen2 == gen + 1 and st.load().last_error is None
    registrations = env["worker"].calls.count(("POST", "/register"))
    service._advance(gen2)
    assert st.load().snippet_applied_at is not None
    # Resumed from the failed step: no second registration, no second A update.
    assert env["worker"].calls.count(("POST", "/register")) == registrations


def test_stale_result_is_discarded_after_disable(env, monkeypatch) -> None:
    gen = _enable()
    with service.route_lock():
        service.disable()
    service._advance(gen)  # an old background task finishing late
    s = st.load()
    assert not s.enabled and s.registered_at is None


def test_two_computers_warning(env) -> None:
    gen = _enable()
    service._advance(gen)
    for _ in range(4):
        service._register(gen, st.load())
    assert "two_computers" in st.load().warnings


def test_no_secrets_in_logs(env, caplog) -> None:
    caplog.set_level(logging.DEBUG)
    env["sidecar"].reload_ok = False
    gen = _enable()
    service._advance(gen)
    service.refresh_job()
    with service.route_lock():
        service.disable()
    service.after_disable()
    s = st.load()
    pem = (st.home_dir() / "device_ed25519.pem").read_text()
    for h in env["worker"].households.values():
        if h.get("bearer"):
            assert h["bearer"] not in caplog.text
    assert "PRIVATE KEY" not in caplog.text and pem not in caplog.text
    assert "public-anti-spam" not in caplog.text
    assert s is not None


def test_route_lock_busy() -> None:
    with service.route_lock():
        import threading

        result: list[str] = []

        def other() -> None:
            try:
                with service.route_lock():
                    result.append("got it")
            except service.Busy:
                result.append("busy")

        t = threading.Thread(target=other)
        t.start()
        t.join()
    assert result == ["busy"]


def test_address_from_request() -> None:
    assert service.address_from_request("192.168.1.20") == "192.168.1.20"
    assert service.address_from_request("192.168.1.20:8080") == "192.168.1.20"
    for host in ("localhost", "8.8.8.8", "lumogis.local", f"x.{ZONE}", None, ""):
        assert service.address_from_request(host) is None


def test_resolver_check_timeout(monkeypatch) -> None:
    import time as _time

    from services.home_dns import service as svc

    def slow(*a, **k):
        _time.sleep(5)
        return []

    monkeypatch.setattr(svc.socket, "getaddrinfo", slow)
    started = _time.monotonic()
    assert svc.resolver_check("x.homes.lumogis.app", "192.168.1.20", deadline=0.5) == "unknown"
    assert _time.monotonic() - started < 2


@pytest.mark.parametrize(
    "answer,expected",
    [(["192.168.1.20"], "ok"), (["192.168.1.99"], "pending"), ([], "blocked")],
)
def test_resolver_check_verdicts(monkeypatch, answer, expected) -> None:
    from services.home_dns import service as svc

    monkeypatch.setattr(
        svc.socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", (ip, 0)) for ip in answer]
    )
    assert svc.resolver_check("x.homes.lumogis.app", "192.168.1.20") == expected


def test_resolver_check_nxdomain(monkeypatch) -> None:
    import socket as _socket

    from services.home_dns import service as svc

    def nx(*a, **k):
        raise _socket.gaierror("NXDOMAIN")

    monkeypatch.setattr(svc.socket, "getaddrinfo", nx)
    assert svc.resolver_check("x.homes.lumogis.app", "192.168.1.20") == "blocked"


def test_audit_rows_written(env, monkeypatch) -> None:
    rows = []
    import actions.audit as audit_mod

    monkeypatch.setattr(
        audit_mod, "write_audit", lambda entry, reverse_token=None: rows.append(entry)
    )
    service.audit("secure_address.enable", "u1", {"lan_address": "192.168.1.20"})
    (row,) = rows
    assert row.connector == "admin" and row.mode == "system" and row.user_id == "u1"
    assert json.loads(row.input_summary) == {"lan_address": "192.168.1.20"}


# --------------------------------------------------------------------------- real TLS probe


def _self_signed(tmp_path: Path, name: str, issuer_org: str | None = None):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    now = datetime.now(timezone.utc).replace(microsecond=0)
    cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)]))
        .issuer_name(
            x509.Name([x509.NameAttribute(NameOID.ORGANIZATION_NAME, issuer_org)])
            if issuer_org
            else x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
        )
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=89))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(name)]), critical=False)
        .sign(key, hashes.SHA256())
    )
    cert_path, key_path = tmp_path / "c.pem", tmp_path / "k.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return cert_path, key_path, now


def test_probe_reads_certificate_dates(tmp_path, monkeypatch) -> None:
    import socket as _socket
    import ssl as _ssl
    import threading

    name = "kq7f3r2abcdefghijklmnopqrs.homes.lumogis.app"
    cert_path, key_path, now = _self_signed(tmp_path, name)
    ctx = _ssl.SSLContext(_ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(cert_path, key_path)
    srv = _socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(2)
    port = srv.getsockname()[1]

    def serve() -> None:
        for _ in range(2):
            conn, _ = srv.accept()
            try:
                with ctx.wrap_socket(conn, server_side=True) as tls:
                    tls.recv(1)
            except (OSError, _ssl.SSLError):
                pass

    threading.Thread(target=serve, daemon=True).start()
    monkeypatch.setenv("LUMOGIS_SECURE_ADDRESS_PROBE_HOST", "127.0.0.1")
    monkeypatch.setenv("LUMOGIS_SECURE_ADDRESS_PROBE_PORT", str(port))
    kind, not_before, not_after = service.probe_certificate(name)
    assert kind == "ok"
    assert not_before == now - timedelta(days=1)
    assert not_after == now + timedelta(days=89)
    # A certificate that doesn't name the household is "no certificate for us".
    assert service.probe_certificate("other.homes.lumogis.app")[0] == "no_cert"
    srv.close()


def test_probe_unreachable(monkeypatch) -> None:
    monkeypatch.setenv("LUMOGIS_SECURE_ADDRESS_PROBE_HOST", "127.0.0.1")
    monkeypatch.setenv("LUMOGIS_SECURE_ADDRESS_PROBE_PORT", "1")
    assert service.probe_certificate("x.homes.lumogis.app")[0] == "unreachable"


def test_turn_off_during_a_worker_call_wins(env, monkeypatch) -> None:
    """A reply that arrives after Turn off must not re-register or re-enable."""
    real_register = wc.register

    def register_then_admin_turns_off(key, **kw):
        result = real_register(key, **kw)
        with service.route_lock():
            service.disable()  # the admin clicks Turn off while we wait on the Worker
        return result

    monkeypatch.setattr(wc, "register", register_then_admin_turns_off)
    gen = _enable()
    service._advance(gen)
    s = st.load()
    assert not s.enabled
    assert s.registered_at is None and s.snippet_applied_at is None
    assert not (env["caddy_home"] / "home.caddy").exists()


# --------------------------------------------------------------------------- §7a review fixes


def test_turn_off_during_caddy_validation_wins(env) -> None:
    """Turn off while Caddy validates: the block is never placed or reloaded in."""

    def admin_turns_off() -> None:
        with service.route_lock():
            service.disable()
        service.after_disable()

    env["sidecar"].on_apply = admin_turns_off
    gen = _enable()
    service._advance(gen)
    s = st.load()
    assert not s.enabled and s.snippet_applied_at is None
    assert not (env["caddy_home"] / "home.caddy").exists()
    assert not (env["caddy_home"] / "home.caddy.pending").exists()
    assert s.last_error is None  # overtaken quietly, not an error


def test_turn_off_just_before_the_snippet_is_written_wins(env, monkeypatch) -> None:
    """Turn off after the budget check, before the pending file exists: the
    generation check under the lock is what stops the block being placed."""
    real_budget = wc.budget

    def budget_then_admin_turns_off(bearer):
        result = real_budget(bearer)
        with service.route_lock():
            service.disable()
        return result

    monkeypatch.setattr(wc, "budget", budget_then_admin_turns_off)
    service._advance(_enable())
    assert not st.load().enabled
    assert not (env["caddy_home"] / "home.caddy").exists()
    assert not (env["caddy_home"] / "home.caddy.pending").exists()


def test_new_address_during_caddy_validation_is_redone(env) -> None:
    """A changed address mid-apply stops the old run; the new generation applies."""

    def admin_changes_address() -> None:
        env["sidecar"].on_apply = None
        with service.route_lock():
            service.set_lan_address("192.168.1.77", None)

    env["sidecar"].on_apply = admin_changes_address
    service._advance(_enable())
    assert st.load().snippet_applied_at is None
    service._advance(st.load().generation)
    s = st.load()
    assert s.snippet_applied_at is not None
    assert env["worker"].households[s.household_id]["a"] == "192.168.1.77"


def test_refresh_removes_a_block_left_behind_while_off(env) -> None:
    """Off, but home.caddy is still on disk (a failed reload, a crash before it)."""
    service._advance(_enable())
    s = st.load()
    s.enabled = False  # what disable() saved before the process died
    st.save(s)
    reloads = env["sidecar"].calls.count("/caddy/reload")
    service.refresh_job()
    assert not (env["caddy_home"] / "home.caddy").exists()
    assert env["sidecar"].calls.count("/caddy/reload") == reloads + 1


def test_heartbeat_failed_reregistration_backs_off(env) -> None:
    service._advance(_enable())
    s = st.load()
    s.last_heartbeat_at = datetime.now(timezone.utc) - timedelta(days=2)
    st.save(s)
    env["worker"].households[s.household_id]["bearer"] = "restored-old-backup"
    env["worker"].fail["/register"] = httpx.Response(503, json={"error": "unavailable"})
    service.refresh_job()  # must not raise or log a traceback loop
    s2 = st.load()
    assert s2.last_error == "worker_unreachable" and s2.next_retry_at is not None
    assert s2.last_heartbeat_at == s.last_heartbeat_at  # not stamped: no answer yet
    registers = env["worker"].calls.count(("POST", "/register"))
    service.refresh_job()  # backing off: no second /register
    assert env["worker"].calls.count(("POST", "/register")) == registers


def test_heartbeat_not_stamped_on_a_transient_error(env) -> None:
    service._advance(_enable())
    s = st.load()
    s.last_heartbeat_at = datetime.now(timezone.utc) - timedelta(days=2)
    st.save(s)
    env["worker"].fail["/budget"] = httpx.Response(503, json={"error": "unavailable"})
    service.refresh_job()
    assert st.load().last_heartbeat_at == s.last_heartbeat_at


def test_a_busy_job_makes_the_next_run_skip(env) -> None:
    service._progress_lock.acquire()
    try:
        service.refresh_job()
    finally:
        service._progress_lock.release()
    assert env["sidecar"].calls == []


def test_fast_run_skips_detection_recovery_and_heartbeat(env) -> None:
    service._advance(_enable())
    before = list(env["sidecar"].calls)
    budget_calls = env["worker"].calls.count(("GET", "/budget"))
    service.refresh_job(fast=True)
    new = env["sidecar"].calls[len(before) :]
    assert "/host-lan-address" not in new and "/caddy/home-status" not in new
    assert env["worker"].calls.count(("GET", "/budget")) == budget_calls


def test_failed_detection_keeps_the_mismatch_warning(env) -> None:
    service._advance(_enable("192.168.1.50"))  # manual; detection says .20
    service.refresh_job()
    assert "address_mismatch" in st.load().warnings
    env["sidecar"].detected = {"address": None, "reason": "failed"}
    service.refresh_job()
    s = st.load()
    assert "address_mismatch" in s.warnings
    assert s.last_detected_address == "192.168.1.20" and s.last_detection_reason == "failed"


def test_refused_address_is_shown_and_cleared_by_a_new_one(env) -> None:
    env["worker"].fail["/a"] = httpx.Response(400, json={"error": "bad_input"})
    gen = _enable()
    service._advance(gen)
    view = service.status(None, sign_in_enabled=True)
    assert (view.state, view.error_code) == ("error", "lan_address_unknown")
    del env["worker"].fail["/a"]
    with service.route_lock():
        gen2 = service.set_lan_address("192.168.1.30", None)
    assert st.load().last_error is None
    service._advance(gen2)
    assert st.load().snippet_applied_at is not None


def test_probe_connection_closed_mid_handshake_is_unreachable(monkeypatch) -> None:
    """Caddy restarting closes the socket: keep the cached certificate."""
    import socket as _socket
    import threading

    srv = _socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)

    def serve() -> None:
        conn, _ = srv.accept()
        conn.recv(65536)  # read the ClientHello, then hang up cleanly (FIN, not RST)
        conn.shutdown(_socket.SHUT_RDWR)
        conn.close()

    threading.Thread(target=serve, daemon=True).start()
    monkeypatch.setenv("LUMOGIS_SECURE_ADDRESS_PROBE_HOST", "127.0.0.1")
    monkeypatch.setenv("LUMOGIS_SECURE_ADDRESS_PROBE_PORT", str(srv.getsockname()[1]))
    assert service.probe_certificate("x.homes.lumogis.app")[0] == "unreachable"
    srv.close()


def test_probe_tls_alert_is_no_cert(tmp_path, monkeypatch) -> None:
    """Caddy without a certificate for the name ends the handshake with an alert."""
    import socket as _socket
    import ssl as _ssl
    import threading

    cert_path, key_path, _now = _self_signed(tmp_path, "unrelated.example.test")
    ctx = _ssl.SSLContext(_ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(cert_path, key_path)
    ctx.sni_callback = lambda sock, name, c: _ssl.ALERT_DESCRIPTION_INTERNAL_ERROR
    srv = _socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)

    def serve() -> None:
        conn, _ = srv.accept()
        try:
            with ctx.wrap_socket(conn, server_side=True) as tls:
                tls.recv(1)
        except (OSError, _ssl.SSLError):
            pass

    threading.Thread(target=serve, daemon=True).start()
    monkeypatch.setenv("LUMOGIS_SECURE_ADDRESS_PROBE_HOST", "127.0.0.1")
    monkeypatch.setenv("LUMOGIS_SECURE_ADDRESS_PROBE_PORT", str(srv.getsockname()[1]))
    assert service.probe_certificate("x.homes.lumogis.app")[0] == "no_cert"
    srv.close()


# --------------------------------------------------------------------------- plan-named cases


def test_refresh_job_updates_a_on_change_only(env) -> None:
    service._advance(_enable())
    s = st.load()
    s.lan_address_source = "detected"
    s.last_detected_address = s.lan_address
    st.save(s)
    puts = lambda: env["worker"].calls.count(("PUT", "/a"))  # noqa: E731
    before = puts()
    service.refresh_job()
    service.refresh_job()
    assert puts() == before  # same address: no Worker write
    env["sidecar"].detected = {"address": "192.168.1.44", "reason": None}
    service.refresh_job()
    assert puts() == before + 1


def test_backoff_after_403_429(env) -> None:
    env["worker"].fail["/register"] = httpx.Response(
        429, json={"error": "rate_limited"}, headers={"Retry-After": "600"}
    )
    service._advance(_enable())
    s = st.load()
    wait = (s.next_retry_at - s.last_error_at).total_seconds()
    assert 590 <= wait <= 610  # Retry-After honoured
    registers = env["worker"].calls.count(("POST", "/register"))
    service.refresh_job()  # before next_retry_at: no call
    assert env["worker"].calls.count(("POST", "/register")) == registers

    env["worker"].fail["/register"] = httpx.Response(403, json={"error": "registration_forbidden"})
    gen = st.load().generation
    waits = []
    for _ in range(3):
        service._advance(gen)
        cur = st.load()
        waits.append((cur.next_retry_at - cur.last_error_at).total_seconds())
    assert cur.last_error == "registration_refused"
    assert waits[0] < waits[1] < waits[2]  # grows without a Retry-After
    registers = env["worker"].calls.count(("POST", "/register"))
    cur.next_retry_at = None
    st.save(cur)
    service.refresh_job()  # sticky: the job does not retry a refused registration
    assert env["worker"].calls.count(("POST", "/register")) == registers


def test_device_key_lost_new_name_flagged(env) -> None:
    service._advance(_enable())
    old = st.load()
    assert service.status(None, sign_in_enabled=True).name_changed is False
    (st.home_dir() / "device_ed25519.pem").unlink()  # workspace key wiped
    old.last_heartbeat_at = datetime.now(timezone.utc) - timedelta(days=2)
    st.save(old)
    env["worker"].households[old.household_id]["bearer"] = "invalidated"
    service.refresh_job()  # heartbeat → 401 → re-register with a new key
    new = st.load()
    assert new.household_id != old.household_id
    assert new.previous_household_id == old.household_id
    view = service.status(None, sign_in_enabled=True)
    assert view.name_changed is True and new.household_id in _snippet(env)


def test_turning_back_on_keeps_the_name(env) -> None:
    service._advance(_enable())
    first = st.load().household_id
    with service.route_lock():
        service.disable()
    service.after_disable()
    assert st.load().bearer is None  # deregistered
    service._advance(_enable())
    s = st.load()
    assert s.household_id == first  # same device key → same name
    assert s.previous_household_id is None


# ---------------------------------------------------------- LUM-996: detection


def _detect(env, address="10.0.0.5", reason=None) -> None:
    env["sidecar"].detected = {"address": address, "reason": reason}
    service._last_detect_done = float("-inf")  # not a "just finished" reuse
    service.detect_now()


def test_detect_now_while_off_suggests_the_detected_address(env) -> None:
    before = st.load().generation
    _detect(env)
    view = service.status("localhost", sign_in_enabled=True)
    assert view.state == "off" and view.suggested_address == "10.0.0.5"
    s = st.load()
    assert s.generation == before and not s.enabled
    assert env["worker"].calls == []


def test_detect_now_records_docker_desktop_while_off(env) -> None:
    _detect(env, None, "docker_desktop")
    view = service.status("localhost", sign_in_enabled=True)
    assert view.detection_unavailable_reason == "docker_desktop"
    assert view.suggested_address is None


def test_request_host_wins_over_detection(env) -> None:
    _detect(env)
    view = service.status("192.168.1.9", sign_in_enabled=True)
    assert view.suggested_address == "192.168.1.9"


@pytest.mark.parametrize("host", ["lumogis.local", "localhost"])
def test_detection_fills_in_for_a_non_lan_host(env, host) -> None:
    _detect(env)
    assert service.status(host, sign_in_enabled=True).suggested_address == "10.0.0.5"


def test_failed_detection_falls_back_to_the_request_host(env) -> None:
    _detect(env)
    _detect(env, None, "failed")
    assert st.load().last_detected_address == "10.0.0.5"
    assert service.status("192.168.1.9", sign_in_enabled=True).suggested_address == "192.168.1.9"


def test_failed_redetect_keeps_the_good_suggestion_for_localhost(env) -> None:
    _detect(env)
    _detect(env, None, "failed")
    view = service.status("localhost", sign_in_enabled=True)
    assert view.suggested_address == "10.0.0.5"


def _on_at(env, address="10.0.0.5"):
    service._advance(_enable(address))
    s = st.load()
    s.lan_address_source = "detected"
    s.last_detected_address = address
    st.save(s)


def test_detected_address_follows_within_the_subnet(env) -> None:
    _on_at(env)
    published = st.load().name_published_at
    puts = env["worker"].calls.count(("PUT", "/a"))
    env["sidecar"].detected = {"address": "10.0.0.9", "reason": None}
    service.refresh_job()
    s = st.load()
    assert s.lan_address == "10.0.0.9" and "address_mismatch" not in s.warnings
    assert env["worker"].calls.count(("PUT", "/a")) == puts + 1
    assert s.name_published_at == published


def test_detected_address_jump_to_another_subnet_only_warns(env) -> None:
    _on_at(env)
    puts = env["worker"].calls.count(("PUT", "/a"))
    env["sidecar"].detected = {"address": "10.8.0.2", "reason": None}
    service.refresh_job()
    s = st.load()
    assert s.lan_address == "10.0.0.5" and "address_mismatch" in s.warnings
    assert env["worker"].calls.count(("PUT", "/a")) == puts
    env["sidecar"].detected = {"address": "10.0.0.5", "reason": None}
    service.refresh_job()
    assert "address_mismatch" not in st.load().warnings


def test_name_published_at_set_once_per_registration(env) -> None:
    service._advance(_enable())
    first = st.load()
    assert first.name_published_at is not None
    # An address change publishes again but the name is not new.
    with service.route_lock():
        gen = service.set_lan_address("192.168.1.30", None)
    service._advance(gen)
    assert st.load().name_published_at == first.name_published_at
    # A re-registration (dead bearer) restarts the window.
    env["worker"].households[first.household_id]["bearer"] = "invalidated"
    with service.route_lock():
        gen = service.set_lan_address("192.168.1.31", None)
    service._advance(gen)
    s = st.load()
    assert s.registered_at > first.registered_at
    assert s.name_published_at > first.name_published_at


def _threaded_detect(env):
    """Thread A holds the helper (on an Event) while thread B calls detect_now."""
    import threading

    started, release = threading.Event(), threading.Event()
    env["sidecar"].on_detect = lambda: (started.set(), release.wait(5))
    a = threading.Thread(target=service.detect_now)
    a.start()
    assert started.wait(5)
    b = threading.Thread(target=service.detect_now)
    b.start()
    import time

    time.sleep(0.2)  # B is now waiting on the lock
    release.set()
    a.join(10)
    b.join(10)
    assert not a.is_alive() and not b.is_alive()


def test_detect_now_waiter_reuses_the_holders_result(env) -> None:
    env["sidecar"].detected = {"address": None, "reason": "failed"}
    _threaded_detect(env)
    assert env["sidecar"].calls.count("/host-lan-address") == 1
    assert st.load().last_detection_reason == "failed"


def test_detect_now_is_single_flight(env) -> None:
    _threaded_detect(env)
    assert env["sidecar"].calls.count("/host-lan-address") == 1
    assert st.load().last_detected_address == "192.168.1.20"


def test_detect_now_does_not_reuse_a_failure(env) -> None:
    env["sidecar"].detected = {"address": None, "reason": "failed"}
    service.detect_now()
    env["sidecar"].detected = {"address": "10.0.0.5", "reason": None}
    service.detect_now()  # immediately: a failure is never reused
    assert env["sidecar"].calls.count("/host-lan-address") == 2
    assert service.status("localhost", sign_in_enabled=True).suggested_address == "10.0.0.5"


def test_detect_now_reuses_a_result_within_5_s(env) -> None:
    service.detect_now()
    service.detect_now()
    assert env["sidecar"].calls.count("/host-lan-address") == 1
    service._last_detect_done -= 6
    service.detect_now()
    assert env["sidecar"].calls.count("/host-lan-address") == 2


def test_detect_now_busy_when_held(env, monkeypatch) -> None:
    monkeypatch.setattr(service, "DETECT_WAIT_SEC", 0.05)
    service._detect_lock.acquire()
    try:
        with pytest.raises(service.Busy):
            service.detect_now()
    finally:
        service._detect_lock.release()
    assert env["sidecar"].calls == []


def test_refresh_skips_detection_while_a_route_detects(env) -> None:
    service._advance(_enable())
    s = st.load()
    s.last_detection_reason = "docker_desktop"
    st.save(s)
    env["sidecar"].calls.clear()
    service._detect_lock.acquire()
    try:
        service.refresh_job()
    finally:
        service._detect_lock.release()
    assert "/host-lan-address" not in env["sidecar"].calls
    assert st.load().last_detection_reason == "docker_desktop"  # not wiped by (None, None)
    assert "/caddy/home-status" in env["sidecar"].calls  # the run otherwise completed


def test_detect_timeouts(env) -> None:
    # stack-control /host-lan-address: docker info 10 s + docker ps 10 s + docker
    # inspect 10 s + docker run 15 s = 45 s (stack-control/main.py).
    assert service.DETECT_TIMEOUT_SEC > 10 + 10 + 10 + 15
    assert service.DETECT_WAIT_SEC > service.DETECT_TIMEOUT_SEC
    service.detect_now()
    service._advance(_enable())
    service.refresh_job()
    assert env["sidecar"].timeouts["/host-lan-address"] == [
        service.DETECT_TIMEOUT_SEC,
        service.JOB_DETECT_TIMEOUT_SEC,
    ]
    assert service.JOB_DETECT_TIMEOUT_SEC == 20


def test_enable_with_detected_address_is_labelled_detected(env) -> None:
    _detect(env, "10.0.0.5")
    with service.route_lock():
        service.enable("10.0.0.5", "localhost")
    assert st.load().lan_address_source == "detected"


# ------------------------------------------------------------- LUM-996: re-apply in place


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _ready_site(env) -> int:
    """Enabled, applied, with a certificate; the name is days old (no name-new window)."""
    gen = _enable()
    service._advance(gen)
    env["probe"]["kind"] = "ok"
    service.refresh_job()
    s = st.load()
    old = datetime.now(timezone.utc) - timedelta(days=3)
    s.registered_at = s.name_published_at = old
    st.save(s)
    return gen


def test_upgrade_without_name_published_at_does_not_restart_name_new(env) -> None:
    """LUM-1004: a state file from before LUM-996 has no ``name_published_at``; the
    first address change after the upgrade must not say "the name is new" for a name
    that has served for days (seen live on Thomas's Mac, 2026-11-18)."""
    _ready_site(env)
    s = st.load()
    s.name_published_at = None  # written by a release before LUM-996
    st.save(s)
    with service.route_lock():
        gen = service.set_lan_address("192.168.1.30", None)
    service._advance(gen)
    s = st.load()
    assert s.name_published_at == s.registered_at
    assert service.status(None, sign_in_enabled=True).name_new_until is None


def test_first_registration_still_opens_the_name_new_window(env) -> None:
    service._advance(_enable())
    s = st.load()
    assert s.name_published_at is not None and s.name_published_at >= s.registered_at
    env["probe"]["kind"] = "ok"
    service.refresh_job()
    assert service.status(None, sign_in_enabled=True).name_new_until is not None


def _applies(env) -> int:
    return env["sidecar"].calls.count("/caddy/apply")


def _reloads(env) -> int:
    return env["sidecar"].calls.count("/caddy/reload")


def test_fingerprint_stored_after_apply(env) -> None:
    service._advance(_enable())
    assert st.load().snippet_fingerprint == _sha(_snippet(env))


def test_staging_switch_reapplies_in_place(env, monkeypatch) -> None:
    _ready_site(env)
    before = st.load()
    applies, reloads = _applies(env), _reloads(env)
    worker_calls = [c for c in env["worker"].calls if c[1] in ("/register", "/a")]
    monkeypatch.setenv("LUMOGIS_HOME_DNS_ACME_STAGING", "true")
    service.refresh_job()
    s = st.load()
    assert (_applies(env), _reloads(env)) == (applies + 1, reloads + 1)
    assert "acme-staging" in _snippet(env)
    assert [c for c in env["worker"].calls if c[1] in ("/register", "/a")] == worker_calls
    assert s.snippet_applied_at > before.snippet_applied_at
    # the fast job restarts, so the next probe runs within 30 s
    assert service.FAST_JOB_ID in env["sched"].ids()
    assert s.registered_at == before.registered_at
    assert s.name_published_at == before.name_published_at
    assert service.status(None, sign_in_enabled=True).name_new_until is None
    assert s.snippet_fingerprint == _sha(_snippet(env)) and s.last_error is None


@pytest.mark.parametrize(
    "var,value,needle",
    [
        ("LUMOGIS_HOME_DNS_URL", "https://dns2.example.test", "dns2.example.test"),
        ("LUMOGIS_SECURE_ADDRESS_PORT", "8443", "8443"),
    ],
)
def test_url_or_port_change_reapplies(env, monkeypatch, var, value, needle) -> None:
    _ready_site(env)
    applies = _applies(env)
    monkeypatch.setenv(var, value)
    service.refresh_job()
    assert _applies(env) == applies + 1
    assert needle in _snippet(env)


def test_unchanged_settings_do_not_reapply(env) -> None:
    _ready_site(env)
    applies = _applies(env)
    service.refresh_job()
    service.refresh_job()
    assert _applies(env) == applies


def test_state_without_fingerprint_reapplies_once(env) -> None:
    _ready_site(env)
    s = st.load()
    s.snippet_fingerprint = None
    st.save(s)
    applies = _applies(env)
    service.refresh_job()
    assert _applies(env) == applies + 1
    assert st.load().snippet_fingerprint == _sha(_snippet(env))
    service.refresh_job()
    assert _applies(env) == applies + 1


def test_reapply_without_certificate_restarts_the_issuing_window(env, monkeypatch) -> None:
    service._advance(_enable())
    s = st.load()
    s.snippet_applied_at = datetime.now(timezone.utc) - timedelta(minutes=14)
    st.save(s)
    monkeypatch.setenv("LUMOGIS_HOME_DNS_ACME_STAGING", "true")
    service.refresh_job()
    assert datetime.now(timezone.utc) - st.load().snippet_applied_at < timedelta(minutes=1)


def test_reapply_rejected_keeps_the_old_site(env, monkeypatch) -> None:
    _ready_site(env)
    old, reloads = _snippet(env), _reloads(env)
    monkeypatch.setenv("LUMOGIS_HOME_DNS_ACME_STAGING", "true")
    env["sidecar"].apply_code = "caddy_snippet_rejected"
    service.refresh_job()
    assert _snippet(env) == old
    assert st.load().last_error == "caddy_snippet_rejected"
    assert _reloads(env) == reloads  # no reload of a removed site


def test_reapply_reload_failure_restores_the_old_site(env, monkeypatch) -> None:
    _ready_site(env)
    before = st.load()
    old, reloads = _snippet(env), _reloads(env)
    monkeypatch.setenv("LUMOGIS_HOME_DNS_ACME_STAGING", "true")
    env["sidecar"].reload_ok = False
    service.refresh_job()
    s = st.load()
    assert _snippet(env) == old
    assert s.snippet_fingerprint == before.snippet_fingerprint == _sha(old)
    assert _reloads(env) == reloads + 1  # no second reload
    assert s.snippet_applied_at == before.snippet_applied_at
    assert s.last_error == "caddy_reload_failed"
    env["sidecar"].reload_ok = True
    s.next_retry_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    st.save(s)
    service.refresh_job()
    assert "acme-staging" in _snippet(env)


def test_reapply_write_back_failure_falls_back_to_the_enable_path(env, monkeypatch) -> None:
    from services import _atomic_file

    _ready_site(env)
    monkeypatch.setenv("LUMOGIS_HOME_DNS_ACME_STAGING", "true")
    env["sidecar"].reload_ok = False
    real = _atomic_file.atomic_write_text

    def failing(path, text, **kw):
        if Path(path).name == "home.caddy":
            raise OSError("disk full")
        return real(path, text, **kw)

    monkeypatch.setattr(_atomic_file, "atomic_write_text", failing)
    service.refresh_job()
    s = st.load()
    assert not (env["caddy_home"] / "home.caddy").exists()
    assert s.snippet_applied_at is None and s.snippet_fingerprint is None
    assert s.last_error == "caddy_reload_failed"
    monkeypatch.setattr(_atomic_file, "atomic_write_text", real)
    env["sidecar"].reload_ok = True
    s.next_retry_at = None
    st.save(s)
    budgets = env["worker"].calls.count(("GET", "/budget"))
    service.refresh_job()
    assert env["worker"].calls.count(("GET", "/budget")) > budgets  # through _advance
    assert (env["caddy_home"] / "home.caddy").exists()


def test_enable_reload_failure_still_removes_the_block(env) -> None:
    env["sidecar"].reload_ok = False
    service._advance(_enable())
    assert not (env["caddy_home"] / "home.caddy").exists()
    assert _reloads(env) == 2
    assert st.load().snippet_fingerprint is None


def test_reapply_unreachable_keeps_the_site_and_retries(env, monkeypatch) -> None:
    _ready_site(env)
    old = _snippet(env)
    monkeypatch.setenv("LUMOGIS_HOME_DNS_ACME_STAGING", "true")
    env["sidecar"].apply_raises = True
    service.refresh_job()
    assert _snippet(env) == old and st.load().last_error == "caddy_unreachable"
    env["sidecar"].apply_raises = False
    s = st.load()
    s.next_retry_at = None
    st.save(s)
    service.refresh_job()
    assert "acme-staging" in _snippet(env)


def test_reapply_skipped_when_sticky_error_or_backing_off(env, monkeypatch) -> None:
    _ready_site(env)
    monkeypatch.setenv("LUMOGIS_HOME_DNS_ACME_STAGING", "true")
    applies = _applies(env)
    s = st.load()
    s.last_error = "clock_skew"
    st.save(s)
    service.refresh_job()
    assert _applies(env) == applies
    s = st.load()
    s.last_error = None
    s.next_retry_at = datetime.now(timezone.utc) + timedelta(minutes=5)
    st.save(s)
    service.refresh_job()
    assert _applies(env) == applies


def test_reapply_overtaken_by_turn_off(env, monkeypatch) -> None:
    _ready_site(env)
    monkeypatch.setenv("LUMOGIS_HOME_DNS_ACME_STAGING", "true")

    def admin_turns_off() -> None:
        with service.route_lock():
            service.disable()
        service.after_disable()

    env["sidecar"].on_apply = admin_turns_off
    service.refresh_job()
    assert not st.load().enabled
    assert not (env["caddy_home"] / "home.caddy").exists()
    assert not (env["caddy_home"] / "home.caddy.pending").exists()


def test_invalid_rendered_value_leaves_the_site_alone(env, monkeypatch, caplog) -> None:
    _ready_site(env)
    old, applies = _snippet(env), _applies(env)
    monkeypatch.setenv("LUMOGIS_HOME_DNS_URL", "http://bad")
    assert service._settings_changed(st.load()) is False
    service.refresh_job()
    assert _applies(env) == applies and _snippet(env) == old
    assert any("invalid site block" in r.getMessage() for r in caplog.records)


def test_invalid_render_warns_once_per_process(env, monkeypatch, caplog) -> None:
    _ready_site(env)
    monkeypatch.setenv("LUMOGIS_HOME_DNS_URL", "http://bad")
    service.refresh_job()
    service.refresh_job()
    assert sum("invalid site block" in r.getMessage() for r in caplog.records) == 1


def test_on_startup_no_check_when_unchanged(env) -> None:
    _ready_site(env)
    service.on_startup()
    assert service.STARTUP_CHECK_JOB_ID not in env["sched"].ids()


def test_disable_clears_the_fingerprint(env) -> None:
    service._advance(_enable())
    assert st.load().snippet_fingerprint
    with service.route_lock():
        service.disable()
    assert st.load().snippet_fingerprint is None


def test_ca_switch_with_a_certificate_reports_issuing(env, monkeypatch) -> None:
    _ready_site(env)
    monkeypatch.setenv("LUMOGIS_HOME_DNS_ACME_STAGING", "true")
    env["probe"]["kind"] = "other_ca"
    before = st.load()
    service.refresh_job()
    s = st.load()
    assert _snippet(env).count("acme-staging") == 1
    assert s.cert_not_after is not None  # kept: step 3 ran before the new block was in place
    assert s.snippet_applied_at > before.snippet_applied_at
    assert service.FAST_JOB_ID in env["sched"].ids()
    assert service.status(None, sign_in_enabled=True).state == "ready"
    service.refresh_job(fast=True)  # the old CA's certificate, the new block in place
    s = st.load()
    assert s.cert_not_after is None
    view = service.status(None, sign_in_enabled=True)
    assert view.state == "issuing" and view.name_new_until is None
    assert service.public_view().url is None
    env["probe"]["kind"] = "ok"  # the requested CA's certificate is served
    service.refresh_job(fast=True)
    assert service.status(None, sign_in_enabled=True).state == "ready"


def test_ca_switch_restarts_caddy_once_the_new_block_is_in_place(env, monkeypatch) -> None:
    """LUM-1004: a reload keeps the other CA's cached certificate (LUM-998 step 0), so
    Caddy is restarted, and only after the block for the requested CA is on disk."""
    _ready_site(env)
    monkeypatch.setenv("LUMOGIS_HOME_DNS_ACME_STAGING", "true")
    env["probe"]["kind"] = "other_ca"
    env["sidecar"].calls.clear()
    service.refresh_job()  # step 3 probes before step 5b re-applies: no restart yet
    calls = env["sidecar"].calls
    assert "/caddy/restart" not in calls
    assert calls.index("/caddy/reload") >= 0
    service.refresh_job(fast=True)  # the new block is in place, the old cert still served
    assert env["sidecar"].calls.count("/caddy/restart") == 1
    service.refresh_job(fast=True)  # within the rate limit: no second restart
    assert env["sidecar"].calls.count("/caddy/restart") == 1
    env["probe"]["kind"] = "ok"
    service.refresh_job(fast=True)
    assert env["sidecar"].calls.count("/caddy/restart") == 1
    assert service.status(None, sign_in_enabled=True).state == "ready"


def test_ca_switch_restart_failure_keeps_following_issuance(env, monkeypatch) -> None:
    _ready_site(env)
    monkeypatch.setenv("LUMOGIS_HOME_DNS_ACME_STAGING", "true")
    env["probe"]["kind"] = "other_ca"
    env["sidecar"].restart_ok = False
    service.refresh_job()
    service.refresh_job(fast=True)
    assert env["sidecar"].calls.count("/caddy/restart") == 1
    view = service.status(None, sign_in_enabled=True)
    assert view.state == "issuing" and view.error_code is None


def test_matching_ca_never_restarts_caddy(env) -> None:
    _ready_site(env)
    env["probe"]["kind"] = "ok"
    service.refresh_job()
    service.refresh_job(fast=True)
    assert "/caddy/restart" not in env["sidecar"].calls


@pytest.mark.parametrize("failure", ["reload_ok", "apply_raises"])
def test_ca_switch_failed_reapply_stays_ready(env, monkeypatch, failure) -> None:
    _ready_site(env)
    old = _snippet(env)
    monkeypatch.setenv("LUMOGIS_HOME_DNS_ACME_STAGING", "true")
    env["probe"]["kind"] = "other_ca"
    if failure == "reload_ok":
        env["sidecar"].reload_ok = False
    else:
        env["sidecar"].apply_raises = True
    url = service.public_view().url
    service.refresh_job()
    s = st.load()
    assert _snippet(env) == old and s.cert_not_after is not None
    view = service.status(None, sign_in_enabled=True)
    assert view.state == "ready" and view.error_code in ("caddy_reload_failed", "caddy_unreachable")
    assert service.public_view().url == url
    service.refresh_job(fast=True)  # the block on disk still renders the old CA
    assert st.load().cert_not_after is not None


def test_probe_reports_other_ca(tmp_path, monkeypatch) -> None:
    import socket as _socket
    import ssl as _ssl
    import threading

    name = "kq7f3r2abcdefghijklmnopqrs.homes.lumogis.app"
    cert_path, key_path, _now = _self_signed(tmp_path, name, issuer_org="(STAGING) Let's Encrypt")
    ctx = _ssl.SSLContext(_ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(cert_path, key_path)
    srv = _socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(4)

    def serve() -> None:
        for _ in range(2):
            conn, _ = srv.accept()
            try:
                with ctx.wrap_socket(conn, server_side=True) as tls:
                    tls.recv(1)
            except (OSError, _ssl.SSLError):
                pass

    threading.Thread(target=serve, daemon=True).start()
    monkeypatch.setenv("LUMOGIS_SECURE_ADDRESS_PROBE_HOST", "127.0.0.1")
    monkeypatch.setenv("LUMOGIS_SECURE_ADDRESS_PROBE_PORT", str(srv.getsockname()[1]))
    monkeypatch.delenv("LUMOGIS_HOME_DNS_ACME_STAGING", raising=False)
    kind, nb, na = service.probe_certificate(name)
    assert kind == "other_ca" and nb is not None and na is not None
    monkeypatch.setenv("LUMOGIS_HOME_DNS_ACME_STAGING", "true")
    assert service.probe_certificate(name)[0] == "ok"
    srv.close()


def test_probe_production_leaf_under_staging_is_other_ca(tmp_path, monkeypatch) -> None:
    import socket as _socket
    import ssl as _ssl
    import threading

    name = "kq7f3r2abcdefghijklmnopqrs.homes.lumogis.app"
    cert_path, key_path, _now = _self_signed(tmp_path, name)  # no (STAGING)
    ctx = _ssl.SSLContext(_ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(cert_path, key_path)
    srv = _socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(4)

    def serve() -> None:
        for _ in range(2):
            conn, _ = srv.accept()
            try:
                with ctx.wrap_socket(conn, server_side=True) as tls:
                    tls.recv(1)
            except (OSError, _ssl.SSLError):
                pass

    threading.Thread(target=serve, daemon=True).start()
    monkeypatch.setenv("LUMOGIS_SECURE_ADDRESS_PROBE_HOST", "127.0.0.1")
    monkeypatch.setenv("LUMOGIS_SECURE_ADDRESS_PROBE_PORT", str(srv.getsockname()[1]))
    monkeypatch.delenv("LUMOGIS_HOME_DNS_ACME_STAGING", raising=False)
    assert service.probe_certificate(name)[0] == "ok"
    monkeypatch.setenv("LUMOGIS_HOME_DNS_ACME_STAGING", "true")
    assert service.probe_certificate(name)[0] == "other_ca"
    srv.close()


def test_missing_block_clears_the_fingerprint_and_reapplies(env) -> None:
    _ready_site(env)
    (env["caddy_home"] / "home.caddy").unlink()
    service.refresh_job()  # present=None → the fake reports the block absent
    assert (env["caddy_home"] / "home.caddy").exists()
    assert st.load().snippet_fingerprint == _sha(_snippet(env))
    # Caddy unreachable: nothing is known, so nothing changes.
    (env["caddy_home"] / "home.caddy").unlink()
    env["sidecar"].home_status_reachable = False
    applies = _applies(env)
    service.refresh_job()
    assert _applies(env) == applies and not (env["caddy_home"] / "home.caddy").exists()


def test_reverted_setting_clears_a_reapply_rejection(env, monkeypatch) -> None:
    _ready_site(env)
    monkeypatch.setenv("LUMOGIS_HOME_DNS_ACME_STAGING", "true")
    env["sidecar"].apply_code = "caddy_snippet_rejected"
    service.refresh_job()
    assert st.load().last_error == "caddy_snippet_rejected"
    monkeypatch.delenv("LUMOGIS_HOME_DNS_ACME_STAGING")
    env["sidecar"].apply_code = None
    applies = _applies(env)
    service.refresh_job()
    assert st.load().last_error is None and _applies(env) == applies
    assert service.status(None, sign_in_enabled=True).state == "ready"


def test_enable_path_rejection_is_not_self_cleared(env) -> None:
    env["sidecar"].apply_code = "caddy_snippet_rejected"
    service._advance(_enable())
    service.refresh_job()
    service.refresh_job()
    assert st.load().last_error == "caddy_snippet_rejected"


def test_startup_after_a_settings_change_reapplies(env, monkeypatch) -> None:
    import threading
    import time

    _ready_site(env)
    monkeypatch.setenv("LUMOGIS_HOME_DNS_ACME_STAGING", "true")
    env["sched"].jobs.clear()
    service.on_startup()
    (job,) = [j for j in env["sched"].jobs if j["id"] == service.STARTUP_CHECK_JOB_ID]
    assert job["trigger"] == "date"
    applies = _applies(env)
    # Another job holds the lock when the one-off fires: it waits instead of dropping.
    service._progress_lock.acquire()
    threading.Timer(0.2, service._progress_lock.release).start()
    started = time.monotonic()
    job["func"]()
    assert time.monotonic() - started >= 0.15
    assert _applies(env) == applies + 1
    assert "acme-staging" in _snippet(env)


def test_refresh_when_free_waits_for_a_running_job(env, monkeypatch) -> None:
    import threading

    _ready_site(env)
    monkeypatch.setenv("LUMOGIS_HOME_DNS_ACME_STAGING", "true")
    applies = _applies(env)
    service._progress_lock.acquire()
    threading.Timer(0.2, service._progress_lock.release).start()
    service.refresh_when_free()
    assert _applies(env) == applies + 1


def test_refresh_job_in_the_same_setup_is_dropped(env, monkeypatch) -> None:
    """The bug refresh_when_free replaces: refresh_job returns at once under the lock."""
    _ready_site(env)
    monkeypatch.setenv("LUMOGIS_HOME_DNS_ACME_STAGING", "true")
    applies = _applies(env)
    service._progress_lock.acquire()
    try:
        service.refresh_job()
    finally:
        service._progress_lock.release()
    assert _applies(env) == applies


def test_reapply_after_fallback_recovery_does_not_repeat(env, monkeypatch) -> None:
    _ready_site(env)
    monkeypatch.setenv("LUMOGIS_HOME_DNS_ACME_STAGING", "true")
    env["sidecar"].rejected_at_start = True
    applies = _applies(env)
    service.refresh_job()
    assert _applies(env) == applies + 1  # step 5 applied; 5b saw a fresh fingerprint


def test_fallback_recovery_failure_clears_the_fingerprint_and_retry_restores(env) -> None:
    _ready_site(env)
    env["sidecar"].rejected_at_start = True
    env["sidecar"].apply_code = "caddy_snippet_rejected"
    service.refresh_job()
    s = st.load()
    assert not (env["caddy_home"] / "home.caddy").exists()
    assert s.snippet_fingerprint is None and s.last_error == "caddy_snippet_rejected"
    env["sidecar"].rejected_at_start = False
    env["sidecar"].apply_code = None
    with service.route_lock():
        service.retry()
    service.refresh_job()
    assert (env["caddy_home"] / "home.caddy").exists()
    assert st.load().snippet_fingerprint == _sha(_snippet(env))


def test_fallback_recovery_reload_failure_does_not_restore(env, monkeypatch) -> None:
    from services import _atomic_file

    _ready_site(env)
    env["sidecar"].rejected_at_start = True
    env["sidecar"].reload_ok = False
    written: list[str] = []
    real = _atomic_file.atomic_write_text

    def spy(path, text, **kw):
        written.append(Path(path).name)
        return real(path, text, **kw)

    monkeypatch.setattr(_atomic_file, "atomic_write_text", spy)
    service.refresh_job()
    assert "home.caddy" not in written  # only home.caddy.pending (and state.json)
    assert not (env["caddy_home"] / "home.caddy").exists()
    s = st.load()
    assert s.snippet_fingerprint is None and s.last_error == "caddy_snippet_rejected"


def test_missing_block_and_rejected_at_start_in_one_run(env) -> None:
    _ready_site(env)
    env["sidecar"].present = False
    env["sidecar"].rejected_at_start = True
    applies = _applies(env)
    service.refresh_job()
    assert _applies(env) == applies + 1
    assert st.load().snippet_fingerprint == _sha(_snippet(env))


def test_resolver_verdict_stores_checked_at(env) -> None:
    service._advance(_enable())
    service.refresh_job(fast=True)
    stamp = st.load().resolver_checked_at
    assert stamp is not None and datetime.now(timezone.utc) - stamp < timedelta(minutes=1)


def test_no_secrets_in_logs_for_reapply_and_detect(env, monkeypatch, caplog) -> None:
    caplog.set_level(logging.DEBUG)
    _ready_site(env)
    monkeypatch.setenv("LUMOGIS_HOME_DNS_ACME_STAGING", "true")
    service.refresh_job()
    service.detect_now()
    s = st.load()
    for secret in (s.bearer, s.snippet_fingerprint, wc.DEFAULT_REGISTRATION_SECRET):
        assert secret and secret not in caplog.text


def test_turn_off_during_a_failing_reapply_reload_wins(env, monkeypatch) -> None:
    """The write-back of the old block must not outlive a Turn off."""
    _ready_site(env)
    monkeypatch.setenv("LUMOGIS_HOME_DNS_ACME_STAGING", "true")
    env["sidecar"].reload_ok = False

    def admin_turns_off() -> None:
        env["sidecar"].on_reload = None
        with service.route_lock():
            service.disable()

    env["sidecar"].on_reload = admin_turns_off
    service.refresh_job()
    assert not st.load().enabled
    assert not (env["caddy_home"] / "home.caddy").exists()


def test_reapply_reload_timeout_is_not_treated_as_a_refusal(env, monkeypatch) -> None:
    """A timeout does not prove Caddy kept the old config, so the old block is not
    written back as if it had: the block is removed and re-applied on the next run."""
    _ready_site(env)
    monkeypatch.setenv("LUMOGIS_HOME_DNS_ACME_STAGING", "true")
    env["sidecar"].reload_raises = True
    service.refresh_job()
    s = st.load()
    assert not (env["caddy_home"] / "home.caddy").exists()
    assert s.snippet_fingerprint is None and s.last_error == "caddy_unreachable"


def test_detect_now_waits_for_the_job_to_record_its_result(env) -> None:
    """The refresh job as the holder: the waiting route returns after the job's
    result is in the state, not before."""
    import threading

    service._advance(_enable())
    started, release = threading.Event(), threading.Event()
    env["sidecar"].detected = {"address": None, "reason": "docker_desktop"}
    env["sidecar"].on_detect = lambda: (started.set(), release.wait(5))
    job = threading.Thread(target=service.refresh_job)
    job.start()
    assert started.wait(5)
    seen: list = []

    def waiter() -> None:
        service.detect_now()
        seen.append(st.load().last_detection_reason)

    w = threading.Thread(target=waiter)
    w.start()
    import time

    time.sleep(0.2)
    release.set()
    job.join(10)
    w.join(10)
    assert seen == ["docker_desktop"]
    assert env["sidecar"].calls.count("/host-lan-address") == 1


@pytest.mark.parametrize(
    "a,b,same",
    [
        ("10.0.0.5", "10.0.0.9", True),
        ("10.0.0.5", "10.8.0.2", False),
        ("192.168.1.5", "192.168.2.5", False),
        ("192.168.1.5", "not-an-ip", False),
        ("192.168.1.5", None, False),
    ],
)
def test_same_lan(a, b, same) -> None:
    """VERIFY-PLAN: planned in test_home_dns_state.py; ``same_lan`` lives in service.py."""
    assert service.same_lan(a, b) is same
    assert service.same_lan(b, a) is same
