# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""LUM-834 chunk A — ``POST /me/connector-credentials/{connector}/test``.

Covers the probe registry (:mod:`services.connector_probes`), the route's error
contract, the limiter, and the two P0 pins the plan's review rounds produced.

**Fakes here return; they do not raise** (§5a). The real
:meth:`adapters.paperless_source.PaperlessAuthProbe.check` catches
``httpx.HTTPError`` and returns ``"unavailable"`` — it never propagates. A fake
that raised would be testing a path production cannot reach, which is the single
most productive test defect in this codebase's recent history. Where an
exception genuinely *can* escape (the route's "never 5xx" catch), the raising
fake is written **adjacent** to the returning one, because the pair is the point.
"""

from __future__ import annotations

import os
import time
from contextlib import contextmanager

import httpx
import jwt
import pytest
from fastapi.testclient import TestClient
from tests.test_connector_credentials_routes import _TEST_FERNET_KEY
from tests.test_connector_credentials_routes import _RoutesFakeStore

_PROBE_PATH = "/api/v1/me/connector-credentials/{c}/test"

#: A documentation-range address. Never routed, so a leaked real request fails
#: loudly rather than reaching someone's server.
_FAKE_IP = "203.0.113.10"
_BASE_URL = "https://paperless.example"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def store(monkeypatch):
    import config as _config
    from services import connector_credentials as ccs

    s = _RoutesFakeStore()
    _config._instances["metadata_store"] = s
    ccs.reset_for_tests()
    yield s
    _config._instances.pop("metadata_store", None)
    ccs.reset_for_tests()


@pytest.fixture
def dev_env(monkeypatch):
    """``AUTH_ENABLED=false`` — the caller is the anonymous ``default`` user."""
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.delenv("LUMOGIS_PUBLIC_ORIGIN", raising=False)
    monkeypatch.delenv("MCP_AUTH_TOKEN", raising=False)
    monkeypatch.setenv("LUMOGIS_CREDENTIAL_KEY", _TEST_FERNET_KEY)
    monkeypatch.delenv("LUMOGIS_CREDENTIAL_KEYS", raising=False)
    monkeypatch.delenv("PAPERLESS_BASE_URL", raising=False)
    monkeypatch.delenv("PAPERLESS_TOKEN", raising=False)
    monkeypatch.delenv("LUMOGIS_CONNECTOR_PROBE_TIMEOUT_S", raising=False)
    _reset_limiter()
    yield
    _reset_limiter()


@pytest.fixture
def auth_env(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTH_SECRET", "test-connector-probe-access-secret")
    monkeypatch.setenv("LUMOGIS_JWT_REFRESH_SECRET", "test-connector-probe-refresh-secret")
    monkeypatch.setenv("ACCESS_TOKEN_TTL_SECONDS", "900")
    monkeypatch.setenv("REFRESH_TOKEN_TTL_SECONDS", "3600")
    monkeypatch.setenv("LUMOGIS_REFRESH_COOKIE_SECURE", "false")
    monkeypatch.delenv("LUMOGIS_PUBLIC_ORIGIN", raising=False)
    monkeypatch.delenv("MCP_AUTH_TOKEN", raising=False)
    monkeypatch.setenv("LUMOGIS_CREDENTIAL_KEY", _TEST_FERNET_KEY)
    monkeypatch.delenv("LUMOGIS_CREDENTIAL_KEYS", raising=False)
    _reset_limiter()
    yield
    _reset_limiter()
    from routes.auth import _reset_rate_limit_for_tests

    _reset_rate_limit_for_tests()


def _reset_limiter() -> None:
    from routes import connector_credentials as routes_cc

    routes_cc._connector_test_limiter._store.clear()


@contextmanager
def _client():
    import main

    with TestClient(main.app) as client:
        yield client


def _mint_jwt(user_id: str, role: str) -> str:
    return jwt.encode(
        {
            "typ": "access",
            "sub": user_id,
            "role": role,
            "iat": int(time.time()),
            "exp": int(time.time()) + 600,
        },
        os.environ["AUTH_SECRET"],
        algorithm="HS256",
    )


# ---------------------------------------------------------------------------
# Doubles
# ---------------------------------------------------------------------------


class _FakeAuthProbe:
    """Stands in for :class:`adapters.paperless_source.PaperlessAuthProbe`.

    **Returns a status string. Never raises** — because the real one never does.
    Records the ``deadline_s`` it was handed so the two-phase budget can be
    asserted rather than assumed.
    """

    seen: list[float] = []

    def __init__(self, status: str) -> None:
        self._status = status

    def __call__(self, connection, *, deadline_s: float):
        _FakeAuthProbe.seen.append(deadline_s)
        self._conn = connection
        return self

    def check(self) -> str:
        return self._status


def _install_probe(monkeypatch, status: str) -> _FakeAuthProbe:
    from adapters import paperless_source

    _FakeAuthProbe.seen = []
    fake = _FakeAuthProbe(status)
    monkeypatch.setattr(paperless_source, "PaperlessAuthProbe", fake)
    return fake


def _install_resolver(monkeypatch, ips: list[str] | None = None) -> None:
    """Fake DNS only — the real SSRF validator still runs on the real code path.

    Patching ``load_connection`` instead would skip the outbound policy entirely,
    and the policy is half of what this route is trusted to inherit.
    """
    from services import outbound_http_url

    monkeypatch.setattr(outbound_http_url, "_resolved_ips", lambda host: list(ips or [_FAKE_IP]))


def _seed_paperless(store, *, user_id: str = "default", payload: dict | None = None) -> None:
    """Write a real encrypted row through the real service."""
    from services import connector_credentials as ccs

    ccs.put_payload(
        user_id,
        "paperless",
        payload or {"base_url": _BASE_URL, "token": "tok-abc"},
        actor="self",
    )


# ---------------------------------------------------------------------------
# Unit — the registry's dispatch, stated per id
# ---------------------------------------------------------------------------


def test_a_working_paperless_credential_verifies(store, dev_env, monkeypatch):
    _install_resolver(monkeypatch)
    _install_probe(monkeypatch, "ok")
    _seed_paperless(store)

    with _client() as client:
        resp = client.post(_PROBE_PATH.format(c="paperless"))

    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "ok": True,
        "code": "verified",
        "detail": "Connected successfully.",
    }


def test_a_rejected_token_is_auth_failed_not_unreachable(store, dev_env, monkeypatch):
    """401 from paperless. Written adjacent to its converse below (§5a).

    The two codes send the user to fix different things — a rejected token is
    not an unreachable host — so conflating them is advice pointed at the wrong
    problem.
    """
    _install_resolver(monkeypatch)
    _install_probe(monkeypatch, "auth")
    _seed_paperless(store)

    with _client() as client:
        body = client.post(_PROBE_PATH.format(c="paperless")).json()

    assert body["code"] == "auth_failed"
    assert body["ok"] is False


def test_an_unreachable_host_is_unreachable_not_auth_failed(store, dev_env, monkeypatch):
    """The converse of the test above. Identical setup, opposite meaning."""
    _install_resolver(monkeypatch)
    _install_probe(monkeypatch, "unavailable")
    _seed_paperless(store)

    with _client() as client:
        body = client.post(_PROBE_PATH.format(c="paperless")).json()

    assert body["code"] == "unreachable"
    assert body["ok"] is False


def test_a_connector_with_no_probe_is_ok_none_not_ok_false(store, dev_env):
    """``ok=False`` would paint a red ✗ on a connector that is perfectly fine."""
    with _client() as client:
        body = client.post(_PROBE_PATH.format(c="ntfy")).json()

    assert body["ok"] is None
    assert body["code"] == "unsupported"


def test_caldav_is_unsupported_in_chunk_a(store, dev_env):
    """The registry's default arm, so "no entry" is a tested state.

    caldav is deferred (its loader tier-walks and cannot be deadline-bounded),
    and this pins that it degrades to ``unsupported`` rather than to a failure.
    """
    with _client() as client:
        body = client.post(_PROBE_PATH.format(c="caldav")).json()

    assert body["ok"] is None
    assert body["code"] == "unsupported"


def test_imap_reports_tested_elsewhere(store, dev_env):
    """Not ``unsupported`` — a probe exists, it just is not on this route."""
    with _client() as client:
        body = client.post(_PROBE_PATH.format(c="imap")).json()

    assert body["ok"] is None
    assert body["code"] == "tested_elsewhere"


def test_smtp_reports_tested_elsewhere_not_unsupported(store, dev_env):
    """The case an "everything else → unsupported" default gets wrong.

    ``smtp`` looks unprobed but is live-tested by the SMTP opt-in's pre-save
    connect test (LUM-650), so "no connection test for this connector" is untrue
    for it — the same argument that earns ``imap`` its own case.
    """
    with _client() as client:
        body = client.post(_PROBE_PATH.format(c="smtp")).json()

    assert body["code"] == "tested_elsewhere"


def test_system_smtp_is_refused_not_answered(store, dev_env):
    """422 ``use_system_mail_admin`` — the same body every sibling verb returns.

    ``system_smtp`` is hidden from ``GET /registry`` so no card renders for it,
    but it passes registry membership and is reachable by direct POST. A
    ``200 unsupported`` would make this the one verb on the surface that answers
    where the others refuse.
    """
    with _client() as client:
        resp = client.post(_PROBE_PATH.format(c="system_smtp"))

    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["code"] == "use_system_mail_admin"


def test_a_missing_credential_is_not_configured(store, dev_env, monkeypatch):
    _install_resolver(monkeypatch)
    _install_probe(monkeypatch, "ok")

    with _client() as client:
        body = client.post(_PROBE_PATH.format(c="paperless")).json()

    assert body["code"] == "not_configured"
    # Not attempted — there was nothing to attempt.
    assert body["ok"] is None


def test_an_undecryptable_row_is_credential_unavailable_not_invalid_config(
    store, dev_env, monkeypatch
):
    """A rotated key and a typo'd URL must not tell the user the same thing.

    The codebase preserves the ``CredentialUnavailable`` / ``ValueError`` split
    deliberately (``caldav_credentials.py:154-160`` says it is kept for exactly
    this kind of caller); collapsing it here would throw that away at the one
    place a user could act on it.
    """
    _install_resolver(monkeypatch)
    _install_probe(monkeypatch, "ok")
    _seed_paperless(store)
    # A row whose payload decrypts but is structurally wrong for paperless.
    from services import connector_credentials as ccs

    ccs.put_payload("default", "paperless", {"base_url": _BASE_URL}, actor="self")

    with _client() as client:
        body = client.post(_PROBE_PATH.format(c="paperless")).json()

    assert body["code"] == "credential_unavailable"
    assert body["ok"] is False


def test_an_ssrf_refusal_is_invalid_config(store, dev_env, monkeypatch):
    """SSRF refusal and an empty field are both ``ValueError`` — one code.

    An earlier draft of the contract mapped this ``ValueError`` to
    ``unreachable`` in one row and ``invalid_config`` in the next.
    """
    monkeypatch.setenv("LUMOGIS_ALLOW_PRIVATE_OUTBOUND_URLS", "false")
    _install_resolver(monkeypatch, ips=["127.0.0.1"])
    _install_probe(monkeypatch, "ok")
    _seed_paperless(store)

    with _client() as client:
        body = client.post(_PROBE_PATH.format(c="paperless")).json()

    assert body["code"] == "invalid_config"
    assert body["ok"] is False


# ---------------------------------------------------------------------------
# The probe is read-only, and bounded in BOTH phases
# ---------------------------------------------------------------------------


class _RecordingHttpx:
    """A stand-in for the ``httpx`` module as the adapter sees it.

    Exercises the **real** :class:`PaperlessAuthProbe` so the verb, the path and
    the header shape are the shipped ones rather than a test's idea of them.
    """

    HTTPError = httpx.HTTPError
    Timeout = httpx.Timeout

    def __init__(self, status_code: int = 200) -> None:
        self.calls: list[dict] = []
        self.client_kwargs: dict = {}
        self._status_code = status_code

    def Client(self, **kwargs):  # noqa: N802 - mirrors httpx.Client
        outer = self
        outer.client_kwargs = dict(kwargs)

        class _C:
            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *_a):
                return False

            def stream(self_inner, method, url, headers=None, **kw):
                outer.calls.append({"method": method, "url": url, "headers": headers or {}})
                return _R(outer._status_code)

            def request(self_inner, method, url, **kw):
                outer.calls.append({"method": method, "url": url, "headers": kw.get("headers", {})})
                return _R(outer._status_code)

        class _R:
            def __init__(self_inner, status_code):
                self_inner.status_code = status_code

            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *_a):
                return False

        return _C()


def test_the_probe_never_mutates(store, dev_env, monkeypatch):
    """ "Lightweight probe" is exactly the phrase under which a write gets added."""
    from adapters import paperless_source

    fake = _RecordingHttpx(200)
    monkeypatch.setattr(paperless_source, "httpx", fake)
    _install_resolver(monkeypatch)
    _seed_paperless(store)

    with _client() as client:
        body = client.post(_PROBE_PATH.format(c="paperless")).json()

    assert body["code"] == "verified"
    assert fake.calls, "the probe made no request at all"
    assert [c["method"] for c in fake.calls] == ["GET"]


def test_the_real_probe_sends_the_pollers_auth_header(store, dev_env, monkeypatch):
    """A drifted header is a 401, which this route reports as ``auth_failed``.

    So a wrong header would tell every user their token was rejected while the
    token was fine — a failure that reads as the user's fault.
    """
    from adapters.paperless_source import PaperlessAuthProbe
    from adapters.paperless_source import PaperlessPoller
    from services.paperless_credentials import PaperlessConnection

    conn = PaperlessConnection(base_url=_BASE_URL, token="tok-abc")
    assert PaperlessAuthProbe(conn, deadline_s=1.0)._headers() == PaperlessPoller(conn)._headers()


def test_the_paperless_probe_passes_a_bounded_resolver(store, dev_env, monkeypatch):
    """Without this, the untimed DNS path returns silently and hangs a click."""
    from services import paperless_credentials

    seen: dict[str, object] = {}
    real = paperless_credentials.load_connection

    def _spy(user_id, *, resolve_host=None):
        seen["resolve_host"] = resolve_host
        return real(user_id, resolve_host=resolve_host)

    _install_resolver(monkeypatch)
    _install_probe(monkeypatch, "ok")
    _seed_paperless(store)
    monkeypatch.setattr(paperless_credentials, "load_connection", _spy)

    with _client() as client:
        client.post(_PROBE_PATH.format(c="paperless"))

    assert seen["resolve_host"] is not None, "load_connection got the default untimed resolver"


def test_the_second_phase_gets_what_is_LEFT_of_the_budget(store, dev_env, monkeypatch):
    """Bounding the resolve is not bounding the probe.

    A host that resolves and then never answers must still return by the
    deadline, which only happens if phase 2 is handed the *remaining* budget
    rather than a fresh full one. The regression this catches is a one-word
    edit: ``deadline_s=probe_deadline_s()``.
    """
    from services import paperless_credentials

    monkeypatch.setenv("LUMOGIS_CONNECTOR_PROBE_TIMEOUT_S", "0.5")
    _install_resolver(monkeypatch)
    _install_probe(monkeypatch, "ok")
    _seed_paperless(store)

    real = paperless_credentials.load_connection

    def _slow(user_id, *, resolve_host=None):
        time.sleep(0.3)
        return real(user_id, resolve_host=resolve_host)

    monkeypatch.setattr(paperless_credentials, "load_connection", _slow)

    with _client() as client:
        client.post(_PROBE_PATH.format(c="paperless"))

    assert _FakeAuthProbe.seen, "phase 2 never ran"
    remaining = _FakeAuthProbe.seen[0]
    assert 0 < remaining < 0.5, f"phase 2 got {remaining}s of a 0.5s budget already 0.3s spent"


def test_a_deadline_already_spent_makes_the_probe_report_unreachable(store, dev_env, monkeypatch):
    """The real adapter's own guard: no budget left means no request at all."""
    from adapters import paperless_source
    from adapters.paperless_source import PaperlessAuthProbe
    from services.paperless_credentials import PaperlessConnection

    fake = _RecordingHttpx(200)
    monkeypatch.setattr(paperless_source, "httpx", fake)
    conn = PaperlessConnection(base_url=_BASE_URL, token="tok-abc")

    assert PaperlessAuthProbe(conn, deadline_s=0.0).check() == "unavailable"
    assert fake.calls == []


# ---------------------------------------------------------------------------
# The round-trip test that closes the gap both P0s lived in
# ---------------------------------------------------------------------------


def test_the_forms_payload_round_trips_through_the_real_loader(store, dev_env, monkeypatch):
    """The one test that kills BOTH P0s, and the only one that can.

    Every other test in this module sits on one side of the ``load_connection``
    seam: the probe tests fake below it, and the form's Vitest asserts against
    the form's own emissions. A form writing ``api_token`` and a probe faked
    below the loader would both stay green while the system was broken end to
    end. So: take the exact payload ``PaperlessForm`` submits, ``PUT`` it
    through the real route, then run the real loader with only the network
    faked.

    The payload keys are read from the backend constant, not written out here.
    A test hard-coding them would have passed against the broken form.
    """
    from services.paperless_credentials import _PAYLOAD_KEYS

    submitted = _paperless_form_payload()
    assert set(submitted) == set(_PAYLOAD_KEYS)

    _install_resolver(monkeypatch)
    _install_probe(monkeypatch, "ok")

    with _client() as client:
        put = client.put(
            "/api/v1/me/connector-credentials/paperless",
            json={"payload": submitted},
        )
        assert put.status_code == 200, put.text
        body = client.post(_PROBE_PATH.format(c="paperless")).json()

    assert body["code"] == "verified", (
        "the payload the form submits does not load back through the real loader"
    )


def _paperless_form_payload() -> dict[str, str]:
    """The payload ``PaperlessForm.tsx`` submits, read out of the component.

    Reading the TSX rather than restating it is the point: a duplicated literal
    is one that drifts, and drift here is precisely the P0 this test exists to
    prevent.
    """
    import re
    from pathlib import Path

    tsx = (
        Path(__file__).resolve().parents[2]
        / "clients/lumogis-web/src/features/credentials/forms/PaperlessForm.tsx"
    )
    src = tsx.read_text(encoding="utf-8")
    block = re.search(r"const payload[^=]*=\s*\{(.*?)\};", src, re.S)
    assert block, "could not find PaperlessForm's submitted payload literal"
    keys = re.findall(r"^\s*([a-z_]+)\s*:", block.group(1), re.M)
    assert keys, f"no payload keys parsed from {block.group(1)!r}"
    return {k: "https://paperless.example" if k == "base_url" else "tok-abc" for k in keys}


# ---------------------------------------------------------------------------
# Negative
# ---------------------------------------------------------------------------


def test_the_detail_never_contains_the_exception_message(store, dev_env, monkeypatch, caplog):
    """The finding that motivated departing from ``_safe_probe_error``.

    ``str(exc)[:200]`` is truncation, not sanitisation: a host embedded in the
    first 200 characters survives it intact. Asserted against the response
    **and** the log, because a leak into either is a leak.
    """
    from services import paperless_credentials

    secret_host = "paperless.internal.corp.example"

    def _boom(user_id, *, resolve_host=None):
        raise ValueError(f"could not reach https://{secret_host}/api/ — connection refused")

    monkeypatch.setattr(paperless_credentials, "load_connection", _boom)
    _seed_paperless(store)

    with _client() as client:
        resp = client.post(_PROBE_PATH.format(c="paperless"))

    assert resp.status_code == 200
    assert secret_host not in resp.text
    assert resp.json()["code"] == "invalid_config"

    # The log half is asserted against the SERVICE, not through the client.
    # The app reconfigures logging during lifespan (`logging_config.configure_logging`
    # installs its own root handler), which displaces caplog's — so an
    # HTTP-level log assertion here passes or fails depending on pytest's
    # capture mode rather than on the code. The service is where the line is
    # emitted and where the guarantee lives.
    import config as _config
    from services import connector_probes as probes

    # The app's lifespan shutdown clears `config._instances`, so the fake
    # metadata store the fixture installed is gone by the time this direct
    # service call runs. It needs one now: LUM-834 chunk B added an
    # `is_disabled` row read to `probe()` — the short-circuit that stops the
    # Test button claiming success against env config for a switched-off
    # connector — and that read happens before any probe function is called.
    _config._instances["metadata_store"] = store

    with caplog.at_level("WARNING", logger="services.connector_probes"):
        outcome = probes.probe("paperless", "default")

    assert outcome.code == "invalid_config"
    assert secret_host not in caplog.text
    assert "ValueError" in caplog.text, "the log dropped the type as well as the message"


def test_an_unknown_connector_is_404(store, dev_env):
    with _client() as client:
        resp = client.post(_PROBE_PATH.format(c="notaconnector"))

    assert resp.status_code == 404, resp.text
    assert resp.json()["detail"]["code"] == "unknown_connector"


def test_a_badly_formed_connector_id_is_400(store, dev_env):
    with _client() as client:
        resp = client.post(_PROBE_PATH.format(c="NOT VALID"))

    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"]["code"] == "bad_connector_id"


def test_the_eleventh_test_in_a_minute_is_429(store, dev_env, monkeypatch):
    _install_resolver(monkeypatch)
    _install_probe(monkeypatch, "ok")
    _seed_paperless(store)

    with _client() as client:
        codes = [client.post(_PROBE_PATH.format(c="paperless")).status_code for _ in range(11)]

    assert codes[:10] == [200] * 10
    assert codes[10] == 429


def test_the_limiter_refuses_before_load_connection(store, dev_env, monkeypatch):
    """A 429 must mean zero decrypts and zero outbound calls.

    A limiter placed *after* the probe still returns 429 and still passes the
    plain rate-limit test above — which is why this one exists separately.
    """
    from services import paperless_credentials

    calls: list[str] = []
    real = paperless_credentials.load_connection

    def _spy(user_id, *, resolve_host=None):
        calls.append(user_id)
        return real(user_id, resolve_host=resolve_host)

    _install_resolver(monkeypatch)
    _install_probe(monkeypatch, "ok")
    _seed_paperless(store)
    monkeypatch.setattr(paperless_credentials, "load_connection", _spy)

    with _client() as client:
        for _ in range(10):
            client.post(_PROBE_PATH.format(c="paperless"))
        before = len(calls)
        probes_before = len(_FakeAuthProbe.seen)
        resp = client.post(_PROBE_PATH.format(c="paperless"))

    assert resp.status_code == 429
    assert len(calls) == before, "the refused request still decrypted a credential"
    assert len(_FakeAuthProbe.seen) == probes_before, "the refused request still went outbound"


def test_a_probe_failure_is_200_not_500(store, dev_env, monkeypatch):
    """The broad ``except Exception`` is what makes "never 5xx" true.

    Written adjacent to the returning fakes above **on purpose** (§5a): the real
    adapter never raises, so this exercises the other half — an exception type
    nobody enumerated, escaping from somewhere below the route. Without an owner
    for it, "never 5xx on a probe failure" is a wish.
    """
    from services import paperless_credentials

    def _boom(user_id, *, resolve_host=None):
        raise RuntimeError("something nobody enumerated")

    monkeypatch.setattr(paperless_credentials, "load_connection", _boom)
    _seed_paperless(store)

    with _client() as client:
        resp = client.post(_PROBE_PATH.format(c="paperless"))

    assert resp.status_code == 200, resp.text
    assert resp.json()["code"] == "unreachable"


def test_an_unauthenticated_caller_is_401(store, auth_env):
    """Real dependency resolution, not an override — an override bypasses the
    dependency under test."""
    with _client() as client:
        resp = client.post(_PROBE_PATH.format(c="paperless"))

    assert resp.status_code == 401, resp.text


def test_an_authenticated_caller_probes_their_own_row_only(store, auth_env, monkeypatch):
    """The path carries no ``user_id``; the caller's own is the only one usable."""
    import services.users as users_svc

    _install_resolver(monkeypatch)
    _install_probe(monkeypatch, "ok")
    users_svc.create_user("probe-a@example.com", "verylongpassword12", "user")
    user_a = users_svc.get_user_by_email("probe-a@example.com")
    assert user_a is not None
    _seed_paperless(store, user_id=user_a.id)

    users_svc.create_user("probe-b@example.com", "verylongpassword12", "user")
    user_b = users_svc.get_user_by_email("probe-b@example.com")
    assert user_b is not None

    with _client() as client:
        a = client.post(
            _PROBE_PATH.format(c="paperless"),
            headers={"Authorization": f"Bearer {_mint_jwt(user_a.id, 'user')}"},
        ).json()
        b = client.post(
            _PROBE_PATH.format(c="paperless"),
            headers={"Authorization": f"Bearer {_mint_jwt(user_b.id, 'user')}"},
        ).json()

    assert a["code"] == "verified"
    assert b["code"] == "not_configured"


# ---------------------------------------------------------------------------
# Edge
# ---------------------------------------------------------------------------


def test_under_auth_off_the_probe_resolves_the_anonymous_user(store, dev_env, monkeypatch):
    """``AUTH_ENABLED=false`` puts every caller on ``default``.

    Worth pinning because the probe reads a *stored* credential: if the caller
    id drifted, the route would report ``not_configured`` for a row that exists.
    """
    from auth import _DEV_USER_ID

    _install_resolver(monkeypatch)
    _install_probe(monkeypatch, "ok")
    _seed_paperless(store, user_id=_DEV_USER_ID)

    with _client() as client:
        body = client.post(_PROBE_PATH.format(c="paperless")).json()

    assert body["code"] == "verified"


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------


def test_an_attempted_probe_is_audited_with_its_verdict(store, dev_env, monkeypatch):
    from services import connector_credentials as ccs

    _install_resolver(monkeypatch)
    _install_probe(monkeypatch, "auth")
    _seed_paperless(store)
    store.audit.clear()

    with _client() as client:
        client.post(_PROBE_PATH.format(c="paperless"))

    tested = [r for r in store.audit if r["action_name"] == ccs.ACTION_CRED_TESTED]
    assert len(tested) == 1
    assert '"ok": false' in tested[0]["result_summary"]
    # Never what it was given.
    assert "tok-abc" not in tested[0]["input_summary"]
    assert _BASE_URL not in tested[0]["input_summary"]


def test_a_not_attempted_outcome_is_not_audited(store, dev_env):
    """Nothing was tested, so there is no event.

    Auditing non-events would fill the log with them and make "was this
    credential ever verified?" unanswerable.
    """
    from services import connector_credentials as ccs

    store.audit.clear()

    with _client() as client:
        client.post(_PROBE_PATH.format(c="ntfy"))
        client.post(_PROBE_PATH.format(c="imap"))
        client.post(_PROBE_PATH.format(c="paperless"))  # not_configured

    assert [r for r in store.audit if r["action_name"] == ccs.ACTION_CRED_TESTED] == []


# ---------------------------------------------------------------------------
# Contract shape
# ---------------------------------------------------------------------------


def test_every_wire_code_has_a_detail_string_and_vice_versa():
    """The ``Literal`` and the copy table must not drift apart.

    A code with no copy is a ``KeyError`` on a request path; copy with no code
    is dead text. Neither is visible without this.
    """
    import typing

    from models.connector_credential import ConnectorTestResponse

    from services import connector_probes as probes

    wire = set(typing.get_args(ConnectorTestResponse.model_fields["code"].annotation))
    assert wire == set(probes._DETAIL), f"wire codes {wire} vs copy {set(probes._DETAIL)}"


def test_the_route_is_not_on_the_admin_router(store, dev_env):
    """The admin mirror is out of scope, and deliberately so.

    ``probe(connector, user_id)`` has no tier axis, so an admin route would have
    tested *the caller's own* credential while presenting as a tier test.
    """
    import main
    from tests.test_auth_phase2 import _iter_api_routes

    # Inside a booted client on purpose: the routers are included during
    # lifespan, so `main.app.routes` at import time is 39 entries and would let
    # this assertion pass against a route table that has not been built yet.
    with _client():
        paths = {r.path for r in _iter_api_routes(main.app)}

    assert "/api/v1/me/connector-credentials/{connector}/test" in paths
    # Narrow to THIS surface on purpose. `/api/v1/admin/system-mail/test` is a
    # different, already-shipped admin test route (the system-mail Send test) —
    # it is prior art for this chunk, not a violation of it.
    admin_mirrors = [
        p for p in paths if p.endswith("/test") and "/admin/" in p and "connector-credentials" in p
    ]
    assert admin_mirrors == []


def test_a_dns_resolve_that_blows_the_deadline_is_unreachable_not_invalid_config(
    store, dev_env, monkeypatch
):
    """The branch LUM-741's non-``OSError`` design exists to make reachable.

    ``validate_outbound_connector_base_url`` swallows ``OSError`` from the
    resolver and returns successfully — fail OPEN — so a ``TimeoutError`` there
    would be an SSRF-validation bypass. ``OutboundResolveDeadline`` is a bare
    ``Exception`` precisely so it propagates out of the loader instead. Without
    a test, this arm is a branch nothing proves is reachable; with one, a slow
    host is reported as unreachable rather than as bad settings.
    """
    from services import connector_credentials as ccs
    from services import outbound_http_url

    def _never_answers(hostname):
        time.sleep(5)
        return [_FAKE_IP]

    monkeypatch.setenv("LUMOGIS_CONNECTOR_PROBE_TIMEOUT_S", "0.5")
    _install_probe(monkeypatch, "ok")
    _seed_paperless(store)
    store.audit.clear()
    # Installed AFTER seeding, so only the probe's own resolve is the slow one.
    monkeypatch.setattr(outbound_http_url, "_resolved_ips", _never_answers)

    started = time.monotonic()
    with _client() as client:
        body = client.post(_PROBE_PATH.format(c="paperless")).json()
    elapsed = time.monotonic() - started

    assert body["code"] == "unreachable", body
    assert body["ok"] is False
    assert _FakeAuthProbe.seen == [], "the probe went outbound against an unvalidated host"
    assert elapsed < 4, f"the 0.5s budget did not bound the resolve ({elapsed:.1f}s)"
    # The response code alone does NOT prove this branch ran: the route's
    # catch-all maps an escaping exception to `unreachable` too. What separates
    # them is that the branch returns an OUTCOME, so the service audits it —
    # while an exception escaping the service is never audited at all.
    assert [r for r in store.audit if r["action_name"] == ccs.ACTION_CRED_TESTED], (
        "the deadline escaped the service instead of being handled as a verdict"
    )


def test_the_probe_refuses_redirects(store, dev_env, monkeypatch):
    """A validated first hop plus a followed redirect is a guard with a hole.

    Pinned as a keyword rather than left to httpx's default, so this stays a
    property of our code and not of a dependency's release notes.
    """
    from adapters import paperless_source
    from adapters.paperless_source import PaperlessAuthProbe
    from services.paperless_credentials import PaperlessConnection

    fake = _RecordingHttpx(302)
    monkeypatch.setattr(paperless_source, "httpx", fake)

    conn = PaperlessConnection(base_url=_BASE_URL, token="tok-abc")
    # A 3xx is "I could not verify this", not a hop to chase.
    assert PaperlessAuthProbe(conn, deadline_s=1.0).check() == "unavailable"
    assert fake.client_kwargs.get("follow_redirects") is False


def test_the_probe_caps_each_phase_at_HALF_the_budget(store, dev_env, monkeypatch):
    """The advertised ceiling has to be an actual ceiling.

    ``httpx.Timeout(x)`` bounds each phase independently, so handing it the full
    budget lets connect-slowly-then-answer-slowly hold the click for ~2× the
    deadline the route promises. This class reads no body, so connect and read
    are the only phases that can elapse — half each makes the total real.
    """
    from adapters import paperless_source
    from adapters.paperless_source import PaperlessAuthProbe
    from services.paperless_credentials import PaperlessConnection

    fake = _RecordingHttpx(200)
    monkeypatch.setattr(paperless_source, "httpx", fake)

    conn = PaperlessConnection(base_url=_BASE_URL, token="tok-abc")
    assert PaperlessAuthProbe(conn, deadline_s=4.0).check() == "ok"
    assert fake.client_kwargs["timeout"].connect == 2.0
    assert fake.client_kwargs["timeout"].read == 2.0


def test_one_implementation_of_the_env_deadline_policy(monkeypatch):
    """The floor and the junk fallback are one policy, not two copies of one.

    LUM-839 shipped a duplication whose comment said "must not drift" and
    enforced nothing. This asserts the shared helper instead of asserting a
    promise about it.
    """
    from adapters.paperless_source import _overlap_deadline_s
    from services.connector_probes import PROBE_TIMEOUT_ENV
    from services.connector_probes import probe_deadline_s

    monkeypatch.setenv(PROBE_TIMEOUT_ENV, "0.01")
    monkeypatch.setenv("PAPERLESS_OVERLAP_TIMEOUT_S", "0.01")
    assert probe_deadline_s() == _overlap_deadline_s() == 0.5  # the shared floor

    monkeypatch.setenv(PROBE_TIMEOUT_ENV, "not-a-number")
    monkeypatch.setenv("PAPERLESS_OVERLAP_TIMEOUT_S", "not-a-number")
    assert probe_deadline_s() == _overlap_deadline_s() == 5.0  # the shared default


def test_the_tested_elsewhere_set_matches_the_one_the_screen_uses():
    """One idea in two languages, pinned rather than asked politely to agree.

    LUM-839 shipped exactly this duplication with a comment saying "must not
    drift", which enforced nothing. Drift here puts a live Test button on a card
    whose only possible answer is "test this somewhere else" — or hides the
    button from a connector that could actually be probed.
    """
    import re
    from pathlib import Path

    from services.connector_probes import _TESTED_ELSEWHERE

    tsx = (
        Path(__file__).resolve().parents[2]
        / "clients/lumogis-web/src/features/me/MeConnectorsView.tsx"
    )
    src = tsx.read_text(encoding="utf-8")
    m = re.search(r"const TESTED_ELSEWHERE = new Set\(\[([^\]]*)\]\)", src)
    assert m, "could not find TESTED_ELSEWHERE in MeConnectorsView.tsx"
    web = {x for x in re.findall(r'"([a-z_]+)"', m.group(1))}
    assert web == set(_TESTED_ELSEWHERE), f"web {web} vs backend {set(_TESTED_ELSEWHERE)}"


def test_the_error_contract_rows_that_promise_a_log_line_produce_one(
    store, dev_env, monkeypatch, caplog
):
    """Two contract rows specify a log and returned silence.

    The adapter RETURNS a status string rather than raising, so the
    "exception type only" rule has no type to log and no message to leak — but
    the row still promises a line, and without one an operator debugging
    "everyone says the test fails" has a 200 response and nothing else. The code
    is the safe thing to record.
    """
    _install_resolver(monkeypatch)
    _seed_paperless(store)

    from services import connector_probes as probes

    # Asserted against the SERVICE for the same reason as the leak test above:
    # the app's lifespan reconfigures logging and displaces caplog's handler, so
    # an HTTP-level assertion here would track pytest's capture mode rather than
    # the code. `LOG_LEVEL` defaults to INFO (`logging_config.py:246`), so both
    # lines ship in a deployment.
    _install_probe(monkeypatch, "auth")
    with caplog.at_level("INFO", logger="services.connector_probes"):
        assert probes.probe("paperless", "default").code == "auth_failed"
    assert "connector_probe.rejected" in caplog.text
    assert "code=auth_failed" in caplog.text

    caplog.clear()
    _install_probe(monkeypatch, "unavailable")
    with caplog.at_level("WARNING", logger="services.connector_probes"):
        assert probes.probe("paperless", "default").code == "unreachable"
    assert "connector_probe.unreachable" in caplog.text

    # Still no credential material, on either line.
    assert "tok-abc" not in caplog.text
    assert _BASE_URL not in caplog.text


# ---------------------------------------------------------------------------
# LUM-834 chunk B — the Test button on a disabled connector
#
# This case was undefined until chunk B, and BOTH answers the route could
# otherwise have given are lies:
#
#   * under AUTH_ENABLED=true  → "No credential saved yet", about a credential
#     that is saved;
#   * under the default AUTH_ENABLED=false with env vars set → "Connected
#     successfully", about a connector the user has just switched off.
#
# The second is the dangerous one, which is why the two tests are adjacent.
# ---------------------------------------------------------------------------


def test_the_probe_says_disabled_not_not_configured(store, auth_env):
    """AUTH ON: the credential exists, so "nothing saved yet" is false."""
    from services import connector_credentials as ccs

    _seed_paperless(store, user_id="alice")
    ccs.set_enabled("alice", "paperless", enabled=False, actor="self")

    with _client() as client:
        resp = client.post(
            _PROBE_PATH.format(c="paperless"),
            headers={"Authorization": f"Bearer {_mint_jwt('alice', 'user')}"},
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["code"] == "disabled"
    assert body["ok"] is None, "not attempted is null, not false — nothing was tried"
    assert "saved" not in body["detail"].lower()


def test_the_probe_does_not_report_success_from_env_for_a_disabled_row(store, dev_env, monkeypatch):
    """AUTH OFF — the actively misleading case, and the default deployment.

    With PAPERLESS_BASE_URL set, a probe that reached `load_connection` would
    fall through to the env credential and could answer "Connected
    successfully" for a connector the user had just turned off. The
    short-circuit therefore has to happen BEFORE the credential load, not
    after it.
    """
    from services import connector_credentials as ccs

    monkeypatch.setenv("PAPERLESS_BASE_URL", _BASE_URL)
    monkeypatch.setenv("PAPERLESS_TOKEN", "env-token")
    _seed_paperless(store)
    ccs.set_enabled("default", "paperless", enabled=False, actor="self")

    with _client() as client:
        resp = client.post(_PROBE_PATH.format(c="paperless"))

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["code"] == "disabled", f"expected disabled, got {body!r}"
    assert body["ok"] is not True, "the probe reported success for a disabled connector"


def test_an_enabled_connector_is_still_probed(store, dev_env, monkeypatch):
    """The twin — otherwise the two tests above pass against a probe that
    answered "disabled" for everything.
    """
    from services import connector_credentials as ccs

    _seed_paperless(store)
    assert ccs.is_disabled("default", "paperless") is False

    with _client() as client:
        resp = client.post(_PROBE_PATH.format(c="paperless"))

    assert resp.status_code == 200, resp.text
    assert resp.json()["code"] != "disabled"


def test_a_not_attempted_disabled_outcome_is_not_audited(store, dev_env):
    """ADR-218 §10: nothing was tested, so there is no event.

    Auditing non-events would make "was this credential ever verified?"
    unanswerable, which is the one question the audit row exists to answer.
    """
    from services import connector_credentials as ccs

    _seed_paperless(store)
    ccs.set_enabled("default", "paperless", enabled=False, actor="self")
    before = [r for r in store.audit if r["action_name"] == ccs.ACTION_CRED_TESTED]

    with _client() as client:
        client.post(_PROBE_PATH.format(c="paperless"))

    after = [r for r in store.audit if r["action_name"] == ccs.ACTION_CRED_TESTED]
    assert after == before, "a not-attempted outcome wrote a test audit row"
