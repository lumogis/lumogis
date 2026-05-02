# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Unit tests for the per-user CalDAV connector credentials chunk.

Pins the locked decisions of
``.cursor/plans/caldav_connector_credentials.plan.md``:

* Registry — ``caldav`` is a member of
  :data:`connectors.registry.CONNECTORS` with a non-empty description
  (D3, SR-1).
* :func:`services.caldav_credentials._validate_payload` — wire-shape
  contract: required keys, types, non-empty strings, D11 URL-shape
  rule (urlparse scheme allowlist + non-empty netloc + no leading /
  trailing whitespace), tolerant of unknown top-level keys.
* :func:`services.caldav_credentials.load_connection` — auth-on /
  auth-off split (Q1, Q-A, Q-B, Q-C, D9/D10), env-fallback semantics,
  decrypt-failure propagation.
* :class:`adapters.calendar_adapter.CalendarAdapter` — drops env-only
  reads, calls ``load_connection``, never raises out, structured
  warning log on every skip path with no exception interpolation
  (D5 security-critical).
* :mod:`signals.calendar_monitor` — ``AUTH_ENABLED=true`` refusal +
  one-shot deprecation INFO (D7), call-time env reads (SR-4),
  ``_LEGACY_USER_ID`` propagation through ``SourceConfig`` and
  ``process_signal`` (D10).

Service-level audit / encrypt round-trip already covered by
:mod:`tests.test_connector_credentials_service` and
:mod:`tests.test_ntfy_runtime` — this module focuses on the
caldav-specific surface.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import pytest

from connectors import registry
from services import connector_credentials as ccs


_TEST_FERNET_KEY = "OlGLYckGIbBSt54y8XVmgb441LgKJWvvYoHnpQ_cv9A="


# ---------------------------------------------------------------------------
# Minimal metadata-store stand-in (matches tests/test_ntfy_runtime.py shape).
# Modeling only the SQL the credential service issues keeps these tests
# free of Postgres while still exercising the real Fernet round-trip.
# ---------------------------------------------------------------------------


class _FakeStore:
    def __init__(self) -> None:
        self.creds: dict[tuple[str, str], dict] = {}

    @staticmethod
    def _norm(q: str) -> str:
        return " ".join(q.split()).lower()

    def fetch_one(self, query: str, params: tuple | None = None):
        q = self._norm(query)
        p = params or ()
        if q.startswith("select ciphertext from user_connector_credentials"):
            uid, conn = p
            row = self.creds.get((uid, conn))
            return {"ciphertext": row["ciphertext"]} if row else None
        if q.startswith(
            "select user_id, connector, created_at, updated_at, "
            "created_by, updated_by, key_version "
            "from user_connector_credentials"
        ):
            uid, conn = p
            row = self.creds.get((uid, conn))
            return dict(row) if row else None
        if q.startswith("insert into user_connector_credentials"):
            uid, conn, ciphertext, key_version, created_by, updated_by = p
            now = datetime.now(timezone.utc)
            row = {
                "user_id": uid,
                "connector": conn,
                "ciphertext": ciphertext,
                "key_version": key_version,
                "created_at": now,
                "updated_at": now,
                "created_by": created_by,
                "updated_by": updated_by,
            }
            self.creds[(uid, conn)] = row
            return {
                "user_id": uid,
                "connector": conn,
                "created_at": now,
                "updated_at": now,
                "created_by": created_by,
                "updated_by": updated_by,
                "key_version": key_version,
            }
        if q.startswith("insert into audit_log"):
            return {"id": 1}
        return None

    def fetch_all(self, query: str, params: tuple | None = None):
        return []

    def execute(self, query: str, params: tuple | None = None):
        return None


@pytest.fixture
def store(monkeypatch):
    import config as _config

    s = _FakeStore()
    _config._instances["metadata_store"] = s
    monkeypatch.setenv("LUMOGIS_CREDENTIAL_KEY", _TEST_FERNET_KEY)
    monkeypatch.delenv("LUMOGIS_CREDENTIAL_KEYS", raising=False)
    ccs.reset_for_tests()
    yield s
    _config._instances.pop("metadata_store", None)
    ccs.reset_for_tests()


def _seed(payload: dict, *, user_id: str = "alice") -> None:
    ccs.put_payload(user_id, registry.CALDAV, payload, actor="self")


_GOOD_PAYLOAD = {
    "base_url": "https://nextcloud.example.com/remote.php/dav/",
    "username": "alice",
    "password": "secret",
}


# ---------------------------------------------------------------------------
# Registry — caldav is canonically registered (D3, SR-1).
# ---------------------------------------------------------------------------


def test_caldav_is_registered():
    assert registry.CALDAV == "caldav"
    assert registry.is_registered(registry.CALDAV)
    registry.require_registered(registry.CALDAV)  # must not raise


def test_caldav_appears_in_registry_iter_with_description():
    """``GET /api/v1/me/connector-credentials/registry`` wire shape includes caldav.

    Pinned because the route projects from
    :func:`registry.iter_registered_with_descriptions` and a missing
    description would surface as a 500 in production.
    """
    rows = registry.iter_registered_with_descriptions()
    matches = [r for r in rows if r["id"] == registry.CALDAV]
    assert len(matches) == 1, rows
    assert matches[0]["description"], matches


# ---------------------------------------------------------------------------
# _validate_payload — wire-shape contract.
# ---------------------------------------------------------------------------


def test_validate_payload_accepts_minimal():
    from services.caldav_credentials import _validate_payload

    base_url, username, password = _validate_payload(dict(_GOOD_PAYLOAD))
    assert base_url == _GOOD_PAYLOAD["base_url"]
    assert username == _GOOD_PAYLOAD["username"]
    assert password == _GOOD_PAYLOAD["password"]


def test_validate_payload_tolerates_unknown_keys():
    """Extra top-level keys are forward-compat (e.g. future ``auth_type``)."""
    from services.caldav_credentials import _validate_payload

    payload: dict[str, Any] = dict(_GOOD_PAYLOAD)
    payload["future_field"] = "ignored"
    base_url, *_ = _validate_payload(payload)
    assert base_url == _GOOD_PAYLOAD["base_url"]


@pytest.mark.parametrize("missing_key", ["base_url", "username", "password"])
def test_validate_payload_missing_required_key_raises_credential_unavailable(missing_key):
    from services.caldav_credentials import _validate_payload

    payload = {k: v for k, v in _GOOD_PAYLOAD.items() if k != missing_key}
    with pytest.raises(ccs.CredentialUnavailable):
        _validate_payload(payload)


@pytest.mark.parametrize("non_string_value", [None, 12, [], {}, True])
def test_validate_payload_non_string_field_raises_credential_unavailable(non_string_value):
    from services.caldav_credentials import _validate_payload

    payload = dict(_GOOD_PAYLOAD)
    payload["password"] = non_string_value
    with pytest.raises(ccs.CredentialUnavailable):
        _validate_payload(payload)


def test_validate_payload_non_dict_raises_credential_unavailable():
    from services.caldav_credentials import _validate_payload

    with pytest.raises(ccs.CredentialUnavailable):
        _validate_payload("not-a-dict")  # type: ignore[arg-type]


@pytest.mark.parametrize("field", ["base_url", "username", "password"])
def test_validate_payload_empty_string_raises_value_error(field):
    """Empty required strings → ``ValueError`` (NOT CredentialUnavailable)."""
    from services.caldav_credentials import _validate_payload

    payload = dict(_GOOD_PAYLOAD)
    payload[field] = ""
    with pytest.raises(ValueError) as exc_info:
        _validate_payload(payload)
    assert not isinstance(exc_info.value, ccs.CredentialUnavailable)


@pytest.mark.parametrize(
    "bad_url",
    [
        "ftp://nextcloud.example/remote.php/dav/",
        "file:///etc/passwd",
        "javascript:alert(1)",
        "  https://nextcloud.example/  ",
        "https:// /dav",
        "https://",
        "not-a-url",
    ],
)
def test_validate_payload_rejects_bad_base_url(bad_url):
    from services.caldav_credentials import _validate_payload

    payload = dict(_GOOD_PAYLOAD)
    payload["base_url"] = bad_url
    with pytest.raises(ValueError):
        _validate_payload(payload)


@pytest.mark.parametrize(
    "good_url",
    [
        "http://caldav.lan/dav/",
        "https://nextcloud.example.com/remote.php/dav/",
        "HTTPS://Nextcloud.Example.com/remote.php/dav/",  # scheme is case-insensitive
    ],
)
def test_validate_payload_accepts_url_variants(good_url):
    from services.caldav_credentials import _validate_payload

    payload = dict(_GOOD_PAYLOAD)
    payload["base_url"] = good_url
    base_url, *_ = _validate_payload(payload)
    assert base_url == good_url


# ---------------------------------------------------------------------------
# load_connection — auth-on / auth-off split.
# ---------------------------------------------------------------------------


def test_load_connection_auth_on_with_row_returns_connection(store, monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.delenv("CALENDAR_CALDAV_URL", raising=False)
    monkeypatch.delenv("CALENDAR_USERNAME", raising=False)
    monkeypatch.delenv("CALENDAR_PASSWORD", raising=False)
    _seed(_GOOD_PAYLOAD, user_id="alice")

    from services.caldav_credentials import load_connection

    conn = load_connection("alice")
    assert conn.base_url == _GOOD_PAYLOAD["base_url"]
    assert conn.username == _GOOD_PAYLOAD["username"]
    assert conn.password == _GOOD_PAYLOAD["password"]


def test_load_connection_auth_on_missing_row_raises_not_configured(store, monkeypatch):
    """AUTH_ENABLED=true never falls back to env (Q-A: fail loud, no auto-migrator)."""
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("CALENDAR_CALDAV_URL", "https://should-be-ignored.example/dav/")
    monkeypatch.setenv("CALENDAR_USERNAME", "ignored")
    monkeypatch.setenv("CALENDAR_PASSWORD", "ignored")

    from services.caldav_credentials import load_connection

    with pytest.raises(ccs.ConnectorNotConfigured):
        load_connection("alice")


def test_load_connection_auth_off_env_fallback(store, monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("CALENDAR_CALDAV_URL", "http://caldav.lan/dav/")
    monkeypatch.setenv("CALENDAR_USERNAME", "envuser")
    monkeypatch.setenv("CALENDAR_PASSWORD", "envpass")

    from services.caldav_credentials import load_connection

    conn = load_connection("default")
    assert conn.base_url == "http://caldav.lan/dav/"
    assert conn.username == "envuser"
    assert conn.password == "envpass"


def test_load_connection_auth_off_row_wins_over_env(store, monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("CALENDAR_CALDAV_URL", "http://env-ignored.lan/dav/")
    monkeypatch.setenv("CALENDAR_USERNAME", "env-ignored")
    monkeypatch.setenv("CALENDAR_PASSWORD", "env-ignored")
    _seed(_GOOD_PAYLOAD, user_id="default")

    from services.caldav_credentials import load_connection

    conn = load_connection("default")
    assert conn.base_url == _GOOD_PAYLOAD["base_url"]
    assert conn.username == _GOOD_PAYLOAD["username"]
    assert conn.password == _GOOD_PAYLOAD["password"]


def test_load_connection_auth_off_no_row_no_env_url_raises(store, monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.delenv("CALENDAR_CALDAV_URL", raising=False)

    from services.caldav_credentials import load_connection

    with pytest.raises(ccs.ConnectorNotConfigured):
        load_connection("default")


def test_load_connection_decrypt_failure_propagates(store, monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    _seed(_GOOD_PAYLOAD, user_id="alice")
    store.creds[("alice", registry.CALDAV)]["ciphertext"] = b"not-a-valid-fernet-token"

    from services.caldav_credentials import load_connection

    with pytest.raises(ccs.CredentialUnavailable):
        load_connection("alice")


def test_load_connection_payload_missing_field_raises_credential_unavailable(store, monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    _seed({"base_url": _GOOD_PAYLOAD["base_url"], "username": "alice"}, user_id="alice")

    from services.caldav_credentials import load_connection

    with pytest.raises(ccs.CredentialUnavailable):
        load_connection("alice")


def test_load_connection_payload_empty_string_raises_value_error(store, monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    payload = dict(_GOOD_PAYLOAD)
    payload["password"] = ""
    # Bypass the registry surface's friendly check to seed a row that the
    # service would otherwise refuse — test the resolver-side guard,
    # not the put-side one.
    import json
    from cryptography.fernet import Fernet

    fernet = Fernet(_TEST_FERNET_KEY.encode())
    store.creds[("alice", registry.CALDAV)] = {
        "user_id": "alice",
        "connector": registry.CALDAV,
        "ciphertext": fernet.encrypt(json.dumps(payload).encode()),
        "key_version": 1,
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
        "created_by": "self",
        "updated_by": "self",
    }

    from services.caldav_credentials import load_connection

    with pytest.raises(ValueError) as exc_info:
        load_connection("alice")
    assert not isinstance(exc_info.value, ccs.CredentialUnavailable)


# ---------------------------------------------------------------------------
# CalendarAdapter._get_connection — never raises, structured skip log.
# ---------------------------------------------------------------------------


def _make_source(user_id: str = "alice") -> Any:
    from models.signals import SourceConfig

    return SourceConfig(
        id="src-1",
        name="Test calendar",
        source_type="caldav",
        url="https://display-only.example/",
        category="calendar",
        active=True,
        poll_interval=3600,
        extraction_method="caldav",
        css_selector_override=None,
        last_polled_at=None,
        last_signal_at=None,
        user_id=user_id,
    )


def test_adapter_get_connection_uses_payload_base_url_over_source_url(store, monkeypatch):
    """``payload.base_url`` wins over ``sources.url`` (SR-2 + critique R1 #4)."""
    monkeypatch.setenv("AUTH_ENABLED", "true")
    _seed(_GOOD_PAYLOAD, user_id="alice")

    from adapters.calendar_adapter import CalendarAdapter

    adapter = CalendarAdapter(_make_source())
    conn = adapter._get_connection()
    assert conn is not None
    assert conn.base_url == _GOOD_PAYLOAD["base_url"]
    assert conn.base_url != "https://display-only.example/"


def test_adapter_get_connection_returns_none_on_missing_row(store, monkeypatch, caplog):
    monkeypatch.setenv("AUTH_ENABLED", "true")

    from adapters.calendar_adapter import CalendarAdapter

    caplog.set_level(logging.WARNING, logger="adapters.calendar_adapter")
    adapter = CalendarAdapter(_make_source())
    assert adapter._get_connection() is None

    skip_records = [r for r in caplog.records if r.message == "caldav: poll skipped"]
    assert len(skip_records) == 1
    rec = skip_records[0]
    assert getattr(rec, "user_id", None) == "alice"
    assert getattr(rec, "connector", None) == "caldav"
    assert getattr(rec, "code", None) == "connector_not_configured"


def test_adapter_get_connection_returns_none_on_decrypt_failure(store, monkeypatch, caplog):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    _seed(_GOOD_PAYLOAD, user_id="alice")
    store.creds[("alice", registry.CALDAV)]["ciphertext"] = b"corrupt-fernet"

    from adapters.calendar_adapter import CalendarAdapter

    caplog.set_level(logging.WARNING, logger="adapters.calendar_adapter")
    adapter = CalendarAdapter(_make_source())
    assert adapter._get_connection() is None

    skip_records = [r for r in caplog.records if r.message == "caldav: poll skipped"]
    assert len(skip_records) == 1
    assert getattr(skip_records[0], "code", None) == "credential_unavailable"


def test_adapter_get_connection_returns_none_on_value_error(store, monkeypatch, caplog):
    """Empty required field maps to ``credential_unavailable`` (a row IS present)."""
    monkeypatch.setenv("AUTH_ENABLED", "true")
    payload = dict(_GOOD_PAYLOAD)
    payload["password"] = ""
    import json
    from cryptography.fernet import Fernet

    fernet = Fernet(_TEST_FERNET_KEY.encode())
    store.creds[("alice", registry.CALDAV)] = {
        "user_id": "alice",
        "connector": registry.CALDAV,
        "ciphertext": fernet.encrypt(json.dumps(payload).encode()),
        "key_version": 1,
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
        "created_by": "self",
        "updated_by": "self",
    }

    from adapters.calendar_adapter import CalendarAdapter

    caplog.set_level(logging.WARNING, logger="adapters.calendar_adapter")
    adapter = CalendarAdapter(_make_source())
    assert adapter._get_connection() is None

    skip_records = [r for r in caplog.records if r.message == "caldav: poll skipped"]
    assert len(skip_records) == 1
    assert getattr(skip_records[0], "code", None) == "credential_unavailable"


def test_adapter_skip_log_does_not_leak_credentials(store, monkeypatch, caplog):
    """Skip-path log must never carry the exception object or its repr.

    Security-critical (D5): the caldav / requests / urllib3 stacks
    can carry credential URLs in ``repr(exc)``. Pin this so a future
    "helpful" ``%s`` interpolation regression cannot ship.
    """
    monkeypatch.setenv("AUTH_ENABLED", "true")
    secret_marker = "supersecretpasswordthatleakedintothelog"
    _seed(
        {
            "base_url": "https://nextcloud.example/dav/",
            "username": "alice",
            "password": secret_marker,
        },
        user_id="alice",
    )
    store.creds[("alice", registry.CALDAV)]["ciphertext"] = b"corrupt-fernet"

    from adapters.calendar_adapter import CalendarAdapter

    caplog.set_level(logging.WARNING, logger="adapters.calendar_adapter")
    adapter = CalendarAdapter(_make_source())
    adapter._get_connection()

    skip_records = [r for r in caplog.records if r.message == "caldav: poll skipped"]
    assert len(skip_records) == 1
    rec = skip_records[0]
    # Hard guards against leak-prone log shapes.
    assert rec.exc_info is None
    assert rec.exc_text is None
    assert secret_marker not in rec.getMessage()
    assert secret_marker not in str(rec.args or "")
    for k, v in (rec.__dict__.get("__extra__", {}) or {}).items():
        assert secret_marker not in str(v), k


def test_adapter_get_connection_caches_per_instance(store, monkeypatch):
    """Two ``_get_connection`` calls on one instance produce one ``get_payload`` call.

    The per-instance cache keeps poll-time CPU bounded and matches
    the plan's "one resolution per poll cycle" contract.
    """
    monkeypatch.setenv("AUTH_ENABLED", "true")
    _seed(_GOOD_PAYLOAD, user_id="alice")

    from adapters import calendar_adapter as adapter_mod

    calls: list[tuple[str, str]] = []
    real_get_payload = ccs.get_payload

    def _spy(user_id: str, connector: str):
        calls.append((user_id, connector))
        return real_get_payload(user_id, connector)

    monkeypatch.setattr(adapter_mod.caldav_credentials.ccs, "get_payload", _spy)

    adapter = adapter_mod.CalendarAdapter(_make_source())
    first = adapter._get_connection()
    second = adapter._get_connection()
    assert first is second
    assert len(calls) == 1


# ---------------------------------------------------------------------------
# calendar_monitor — AUTH_ENABLED gate, idempotent INFO, call-time env reads.
# ---------------------------------------------------------------------------


def test_calendar_monitor_refuses_to_schedule_under_auth_enabled(store, monkeypatch, caplog):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("CALENDAR_CALDAV_URL", "https://should-be-ignored.example/dav/")

    import config as _config
    from signals import calendar_monitor

    calendar_monitor._AUTH_DISABLED_LOGGED = False  # reset between cases
    captured_jobs: list[tuple] = []

    class _CapturingScheduler:
        running = True
        def add_job(self, *a, **kw):
            captured_jobs.append((a, kw))
        def get_job(self, _id):
            return None
        def get_jobs(self):
            return []

    _config._instances["scheduler"] = _CapturingScheduler()
    caplog.set_level(logging.INFO, logger="signals.calendar_monitor")

    calendar_monitor.start()
    calendar_monitor.start()  # second call must be silent (idempotent)

    assert captured_jobs == []
    info_lines = [
        r for r in caplog.records
        if r.name == "signals.calendar_monitor" and r.levelno == logging.INFO
    ]
    deprecations = [r for r in info_lines if "AUTH_ENABLED=true" in r.getMessage()]
    assert len(deprecations) == 1, [r.getMessage() for r in info_lines]
    calendar_monitor.stop()


def test_calendar_monitor_env_reads_are_call_time(store, monkeypatch):
    """``start()`` must NOT cache module-level env reads (SR-4)."""
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.delenv("CALENDAR_CALDAV_URL", raising=False)

    import config as _config
    from signals import calendar_monitor

    captured: list[tuple] = []

    class _CapturingScheduler:
        running = True
        def add_job(self, *a, **kw):
            captured.append((a, kw))
        def get_job(self, _id):
            return None

    _config._instances["scheduler"] = _CapturingScheduler()
    calendar_monitor._AUTH_DISABLED_LOGGED = False

    # First call: env var unset → no job scheduled.
    calendar_monitor.start()
    assert captured == []

    # Set env var and call again: job IS scheduled (proves no module
    # caching; the env value was read on this second call).
    monkeypatch.setenv("CALENDAR_CALDAV_URL", "http://caldav.lan/dav/")
    calendar_monitor.start()
    assert len(captured) == 1
    calendar_monitor.stop()


def test_calendar_monitor_poll_uses_legacy_user_id(store, monkeypatch):
    """``_poll_calendar`` must propagate ``_LEGACY_USER_ID="default"`` (D10)."""
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("CALENDAR_CALDAV_URL", "http://caldav.lan/dav/")
    monkeypatch.setenv("CALENDAR_USERNAME", "envuser")
    monkeypatch.setenv("CALENDAR_PASSWORD", "envpass")

    from signals import calendar_monitor

    captured_sources: list = []
    captured_process_user_ids: list[str] = []

    class _StubAdapter:
        def __init__(self, source):
            captured_sources.append(source)
        def poll(self):
            from models.signals import Signal
            return [
                Signal(
                    signal_id="caldav:default:abc",
                    source_id="__caldav__",
                    title="Upcoming: thing",
                    url="",
                    published_at=None,
                    content_summary="",
                    raw_content="",
                    entities=[],
                    topics=["calendar", "event"],
                    importance_score=0.0,
                    relevance_score=0.0,
                    notified=False,
                    created_at=datetime.now(timezone.utc),
                    user_id="default",
                ),
            ]

    def _stub_process(signal, *, user_id):
        captured_process_user_ids.append(user_id)

    monkeypatch.setattr(calendar_monitor, "CalendarAdapter", _StubAdapter)
    monkeypatch.setattr(calendar_monitor, "process_signal", _stub_process)
    monkeypatch.setattr(calendar_monitor, "_enrich_entities", lambda _t: [])

    calendar_monitor._poll_calendar()

    assert len(captured_sources) == 1
    src = captured_sources[0]
    assert src.user_id == calendar_monitor._LEGACY_USER_ID == "default"
    assert captured_process_user_ids == ["default"]
