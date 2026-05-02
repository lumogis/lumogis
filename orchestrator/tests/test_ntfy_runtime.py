# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Tests for services/ntfy_runtime.py.

Pins the ADR 018 D3 split for the ntfy connector:

* ``AUTH_ENABLED=true`` + missing row  → ``ConnectorNotConfigured``;
  the legacy ``NTFY_TOPIC`` / ``NTFY_TOKEN`` env vars MUST NOT be
  consulted; the optional non-secret ``NTFY_URL`` may default a
  payload-supplied ``url`` only when a row IS present.
* ``AUTH_ENABLED=false`` + missing row → env-fallback assembly,
  matching pre-migration behavior for single-user dev installs.
* Decrypt failure                       → ``CredentialUnavailable``
  (propagated from the underlying service).
"""

from __future__ import annotations

import pytest

from services import connector_credentials as ccs


_TEST_FERNET_KEY = "OlGLYckGIbBSt54y8XVmgb441LgKJWvvYoHnpQ_cv9A="


class _FakeStore:
    """Minimal metadata-store stand-in for ntfy_runtime tests.

    Only the two SELECT shapes the loader path issues need to be
    modeled (``get_payload`` is the only credential-credential
    service entry point used here). Audit writes from
    :func:`put_payload` go nowhere — the tests pre-seed ciphertext
    via the service so we exercise the real Fernet round-trip.
    """

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
            from datetime import datetime, timezone
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


def _seed(store, user_id: str, payload: dict) -> None:
    ccs.put_payload(user_id, "ntfy", payload, actor="self")


# ---------------------------------------------------------------------------
# AUTH_ENABLED=true — strict, no env fallback for secrets/topic.
# ---------------------------------------------------------------------------


def test_auth_enabled_with_row_returns_payload(store, monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.delenv("NTFY_URL", raising=False)
    monkeypatch.delenv("NTFY_TOPIC", raising=False)
    monkeypatch.delenv("NTFY_TOKEN", raising=False)
    _seed(store, "alice", {"url": "http://ntfy.lan:8088", "topic": "alice", "token": "tok"})

    from services.ntfy_runtime import load_ntfy_runtime_config

    cfg = load_ntfy_runtime_config("alice")
    assert cfg == {"url": "http://ntfy.lan:8088", "topic": "alice", "token": "tok"}


def test_auth_enabled_url_defaults_from_env(store, monkeypatch):
    """A row may omit ``url`` — non-secret default falls through from NTFY_URL."""
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("NTFY_URL", "http://ntfy.lan:9000/")
    _seed(store, "alice", {"topic": "alice"})

    from services.ntfy_runtime import load_ntfy_runtime_config

    cfg = load_ntfy_runtime_config("alice")
    assert cfg["url"] == "http://ntfy.lan:9000"
    assert cfg["topic"] == "alice"
    assert cfg["token"] == ""


def test_auth_enabled_missing_row_raises_not_configured(store, monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    # Even with env vars set, AUTH=true means no env fallback for delivery.
    monkeypatch.setenv("NTFY_URL", "http://ntfy:80")
    monkeypatch.setenv("NTFY_TOPIC", "should_be_ignored")
    monkeypatch.setenv("NTFY_TOKEN", "should_be_ignored")

    from services.ntfy_runtime import load_ntfy_runtime_config

    with pytest.raises(ccs.ConnectorNotConfigured):
        load_ntfy_runtime_config("alice")


def test_auth_enabled_row_without_topic_raises(store, monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    _seed(store, "alice", {"url": "http://ntfy:80"})

    from services.ntfy_runtime import load_ntfy_runtime_config

    with pytest.raises(ccs.ConnectorNotConfigured):
        load_ntfy_runtime_config("alice")


# ---------------------------------------------------------------------------
# AUTH_ENABLED=false — env fallback honored when no row.
# ---------------------------------------------------------------------------


def test_auth_disabled_env_fallback_assembles_config(store, monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("NTFY_URL", "http://ntfy:80/")
    monkeypatch.setenv("NTFY_TOPIC", "lumogis")
    monkeypatch.setenv("NTFY_TOKEN", "envtok")

    from services.ntfy_runtime import load_ntfy_runtime_config

    cfg = load_ntfy_runtime_config("default")
    assert cfg == {"url": "http://ntfy:80", "topic": "lumogis", "token": "envtok"}


def test_auth_disabled_row_wins_over_env(store, monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("NTFY_URL", "http://env-ignored:80")
    monkeypatch.setenv("NTFY_TOPIC", "env-ignored")
    monkeypatch.setenv("NTFY_TOKEN", "env-ignored")
    _seed(store, "default", {"url": "http://row:80", "topic": "row-topic", "token": "rowtok"})

    from services.ntfy_runtime import load_ntfy_runtime_config

    cfg = load_ntfy_runtime_config("default")
    assert cfg == {"url": "http://row:80", "topic": "row-topic", "token": "rowtok"}


def test_auth_disabled_no_row_no_env_topic_raises(store, monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.delenv("NTFY_TOPIC", raising=False)
    monkeypatch.setenv("NTFY_URL", "http://ntfy:80")

    from services.ntfy_runtime import load_ntfy_runtime_config

    with pytest.raises(ccs.ConnectorNotConfigured):
        load_ntfy_runtime_config("default")


# ---------------------------------------------------------------------------
# Decrypt failures propagate as CredentialUnavailable.
# ---------------------------------------------------------------------------


def test_decrypt_failure_propagates(store, monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    _seed(store, "alice", {"topic": "alice"})
    # Corrupt the row so Fernet rejects it.
    row = store.creds[("alice", "ntfy")]
    row["ciphertext"] = b"not-a-valid-fernet-token"

    from services.ntfy_runtime import load_ntfy_runtime_config

    with pytest.raises(ccs.CredentialUnavailable):
        load_ntfy_runtime_config("alice")
