# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""LUM-834 chunk B — the gate seen from the consumers, not from the primitive.

## Why this file exists separately from ``test_connector_enabled_gate.py``

Those tests assert that ``get_payload`` returns ``None`` and that the tier walk
stops. **Both could pass while the feature does not work.** The chunk's promise
is *"disabling stops ingest and actions"*, and the code that ingests and acts
does not call ``get_payload`` — it calls a loader, and every loader in this
codebase reads ``None`` as *"no row, use the env fallback"*.

Under the default ``AUTH_ENABLED=false`` that fallback is live. So without the
per-loader guards, a user switching paperless off would have kept ingesting
from ``PAPERLESS_BASE_URL`` with a green suite behind it.

Each test below therefore comes in a pair: the disabled row must not reach the
env, and the **absent** row must still reach it, because that is existing
single-user behaviour this chunk is not allowed to break.
"""

from __future__ import annotations

import pytest
from connectors.registry import CALDAV
from connectors.registry import IMAP
from connectors.registry import NTFY
from connectors.registry import PAPERLESS
from tests.test_connector_credentials_service import _FakeStore

from services import connector_credentials as ccs

_TEST_FERNET_KEY = "OlGLYckGIbBSt54y8XVmgb441LgKJWvvYoHnpQ_cv9A="


@pytest.fixture
def store(monkeypatch):
    import config as _config

    monkeypatch.setenv("LUMOGIS_CREDENTIAL_KEY", _TEST_FERNET_KEY)
    monkeypatch.delenv("LUMOGIS_CREDENTIAL_KEYS", raising=False)
    # AUTH OFF is the default deployment and the one where every env fallback
    # is reachable. Testing only the auth-on path would go green over a toggle
    # that changes nothing for most users.
    monkeypatch.setenv("AUTH_ENABLED", "false")
    s = _FakeStore()
    _config._instances["metadata_store"] = s
    ccs.reset_for_tests()
    yield s
    _config._instances.pop("metadata_store", None)
    ccs.reset_for_tests()


def _seed_and_disable(connector: str, payload: dict, *, instance: str = "") -> None:
    ccs.put_payload("alice", connector, payload, actor="self", instance=instance)
    ccs.set_enabled("alice", connector, enabled=False, actor="self", instance=instance)


# ---------------------------------------------------------------------------
# paperless — the branch that made the whole toggle a no-op
# ---------------------------------------------------------------------------


def test_paperless_ingest_does_not_fall_back_to_env_for_a_disabled_row(store, monkeypatch):
    """The default-mode no-op, pinned.

    `paperless_credentials.load_connection` reads `get_payload`, and when that
    returns None falls through `if auth_enabled(): raise` to PAPERLESS_BASE_URL
    / PAPERLESS_TOKEN. `AUTH_ENABLED` defaults to false, so before the guard a
    disabled paperless kept ingesting from the env-configured server and the
    toggle looked broken.
    """
    from services import paperless_credentials

    monkeypatch.setenv("PAPERLESS_BASE_URL", "https://paperless.env.example")
    monkeypatch.setenv("PAPERLESS_TOKEN", "env-token")
    _seed_and_disable(PAPERLESS, {"base_url": "https://paperless.example", "token": "t"})

    with pytest.raises(ccs.ConnectorNotConfigured) as exc:
        paperless_credentials.load_connection("alice")
    assert "disabled" in str(exc.value)


def test_paperless_absent_row_still_uses_the_env(store, monkeypatch):
    """The twin. Without it, the test above passes against a loader whose env
    fallback was deleted outright — which would break every single-user dev
    deployment.
    """
    from services import paperless_credentials

    monkeypatch.setenv("PAPERLESS_BASE_URL", "https://paperless.env.example")
    monkeypatch.setenv("PAPERLESS_TOKEN", "env-token")

    conn = paperless_credentials.load_connection("nobody")
    assert conn.base_url == "https://paperless.env.example"
    assert conn.token == "env-token"


# ---------------------------------------------------------------------------
# ntfy — the residue R1 named
# ---------------------------------------------------------------------------


def test_the_ntfy_env_fallback_is_NOT_used_for_a_disabled_row(store, monkeypatch):
    """`ntfy_runtime` catches ConnectorNotConfigured and reads it as "try env".

    Right for absent, wrong for disabled — and the catch means the resolver's
    own tier-1 raise is swallowed here, so this loader needs its own check.
    """
    from services import ntfy_runtime

    monkeypatch.setenv("NTFY_TOPIC", "env-topic")
    monkeypatch.setenv("NTFY_URL", "http://ntfy.env")
    _seed_and_disable(NTFY, {"topic": "mine", "url": "http://ntfy.mine"})

    with pytest.raises(ccs.ConnectorNotConfigured):
        ntfy_runtime.load_ntfy_runtime_config("alice")


def test_ntfy_absent_row_still_uses_the_env(store, monkeypatch):
    from services import ntfy_runtime

    monkeypatch.setenv("NTFY_TOPIC", "env-topic")
    monkeypatch.setenv("NTFY_URL", "http://ntfy.env")

    cfg = ntfy_runtime.load_ntfy_runtime_config("nobody")
    assert cfg["topic"] == "env-topic"


# ---------------------------------------------------------------------------
# caldav
# ---------------------------------------------------------------------------


def test_calendar_polling_skips_a_disabled_connector(store, monkeypatch):
    from services import caldav_credentials

    monkeypatch.setenv("CALENDAR_CALDAV_URL", "https://dav.env.example/")
    monkeypatch.setenv("CALENDAR_USERNAME", "envuser")
    monkeypatch.setenv("CALENDAR_PASSWORD", "envpass")
    _seed_and_disable(
        CALDAV, {"base_url": "https://dav.example/", "username": "u", "password": "p"}
    )

    with pytest.raises(ccs.ConnectorNotConfigured):
        caldav_credentials.load_connection("alice")


def test_caldav_absent_row_still_uses_the_env(store, monkeypatch):
    from services import caldav_credentials

    monkeypatch.setenv("CALENDAR_CALDAV_URL", "https://dav.env.example/")
    monkeypatch.setenv("CALENDAR_USERNAME", "envuser")
    monkeypatch.setenv("CALENDAR_PASSWORD", "envpass")

    conn = caldav_credentials.load_connection("nobody")
    assert conn.base_url == "https://dav.env.example/"


# ---------------------------------------------------------------------------
# imap — two branches, both unreachable through the API today
# ---------------------------------------------------------------------------
#
# Seeded by writing the row directly, because that is the only way these rows
# exist: `_validate_instance(..., write=True)` requires a NON-EMPTY instance for
# a multi-instance connector, so no write path can create the legacy
# `instance=""` row that migration 054 left behind (LUM-778). The env fallbacks
# below are reachable only for that row, so exercising them through a normal PUT
# is impossible by construction rather than by omission.


def _seed_legacy_imap_row(store, payload: dict, *, enabled: bool) -> None:
    from datetime import datetime
    from datetime import timezone

    now = datetime.now(timezone.utc)
    store.rows[("alice", IMAP, "")] = {
        "user_id": "alice",
        "connector": IMAP,
        "instance": "",
        "ciphertext": ccs._encrypt_payload(payload),
        "key_version": 1,
        "created_at": now,
        "updated_at": now,
        "created_by": "self",
        "updated_by": "self",
        "delivery_paused": False,
        "delivery_paused_reason": None,
        "delivery_paused_detail": None,
        "delivery_paused_at": None,
        "enabled": enabled,
        "configured_keys": sorted(payload.keys()),
    }


_IMAP_PAYLOAD = {
    "host": "imap.example",
    "port": 993,
    "username": "mine@example.com",
    "password": "p",
    "use_ssl": True,
    "auth_type": "app_password",
}


def test_imap_load_connection_does_not_fall_back_to_env_for_a_disabled_row(store, monkeypatch):
    """Guarded now, unreachable now.

    The PATCH that sets `enabled` refuses multi-instance connectors with a 422,
    so no imap row can currently be switched off. This pins the guard for the
    chunk that adds per-instance disable rather than leaving the trap for it to
    find — the trap being an env credential silently substituted for the one the
    user turned off.
    """
    from services import email_credentials

    monkeypatch.setenv("EMAIL_IMAP_HOST", "imap.env.example")
    monkeypatch.setenv("EMAIL_IMAP_USERNAME", "envuser")
    monkeypatch.setenv("EMAIL_IMAP_PASSWORD", "envpass")
    _seed_legacy_imap_row(store, _IMAP_PAYLOAD, enabled=False)

    with pytest.raises(ccs.ConnectorNotConfigured):
        email_credentials.load_connection("alice", instance="")


def test_imap_absent_row_reaches_the_env_path_rather_than_refusing(store, monkeypatch):
    """The twin for `load_connection`, asserted by WHICH failure it produces.

    The happy path cannot be asserted here: the env branch runs the real SSRF
    check, which resolves the host over DNS, and this container has none. So
    the twin pins the fork rather than the result — an absent row must reach
    the env branch (and fail on DNS) rather than being refused as disabled.
    A guard that refused both would fail this with ConnectorNotConfigured.
    """
    from services import email_credentials

    monkeypatch.setenv("EMAIL_IMAP_HOST", "imap.env.example")
    monkeypatch.setenv("EMAIL_IMAP_USERNAME", "envuser")
    monkeypatch.setenv("EMAIL_IMAP_PASSWORD", "envpass")

    with pytest.raises(email_credentials.ImapHostUnresolvedError):
        email_credentials.load_connection("nobody", instance="")


def test_mailbox_address_does_not_fall_back_to_env_for_a_disabled_row(store, monkeypatch):
    """The sixth env-fallback branch — the one an enumeration found and a count missed.

    `mailbox_account_address` carries its own EMAIL_IMAP_USERNAME fallback two
    hundred lines below `load_connection`, with the in-file comment "Mirror
    load_connection's env fallback". Same class, same file, separate branch.
    """
    from services import email_credentials

    monkeypatch.setenv("EMAIL_IMAP_USERNAME", "envuser")
    _seed_legacy_imap_row(store, _IMAP_PAYLOAD, enabled=False)

    with pytest.raises(ccs.ConnectorNotConfigured):
        email_credentials.mailbox_account_address("alice", instance="")


def test_the_legacy_row_borrow_reports_disabled_not_missing(store, monkeypatch):
    """Asserts the MESSAGE, because asserting the type pins nothing here.

    Found by running the §5b mutation: removing the borrow guard killed no test.
    The unguarded code raises `ConnectorNotConfigured` anyway — the env fallback
    below is fenced by `if auth_enabled() or instance != ""`, and this branch
    only runs when `instance != ""`, so the environment was never reachable.
    An earlier version of this test asserted only the exception type and was
    therefore green with the guard deleted.

    What the guard actually buys is a true sentence: without it, a disabled
    legacy row reports "no imap credential row" about a row that exists, and an
    operator goes looking for a missing credential instead of a switched-off
    one. That is what this now pins.
    """
    from services import email_credentials

    monkeypatch.setenv("EMAIL_IMAP_USERNAME", "envuser")
    _seed_legacy_imap_row(store, _IMAP_PAYLOAD, enabled=False)

    with pytest.raises(ccs.ConnectorNotConfigured) as exc:
        email_credentials.mailbox_account_address("alice", instance="mbox-with-no-row")
    assert "disabled" in str(exc.value), (
        f"reported {str(exc.value)!r} — a row that exists must not be described as missing"
    )


def test_the_legacy_row_borrow_still_works_when_enabled(store, monkeypatch):
    """The twin: the borrow is existing behaviour and this chunk must not break it."""
    from services import email_credentials

    monkeypatch.setenv("EMAIL_IMAP_USERNAME", "envuser")
    _seed_legacy_imap_row(store, _IMAP_PAYLOAD, enabled=True)

    assert (
        email_credentials.mailbox_account_address("alice", instance="mbox-with-no-row")
        == "mine@example.com"
    )


def test_mailbox_address_absent_row_still_uses_the_env(store, monkeypatch):
    from services import email_credentials

    monkeypatch.setenv("EMAIL_IMAP_USERNAME", "envuser")
    assert email_credentials.mailbox_account_address("nobody", instance="") == "envuser"


# ---------------------------------------------------------------------------
# llm — its guard is the tier-1 check, not a loader guard
# ---------------------------------------------------------------------------


def test_disabling_an_llm_connector_stops_chat_resolving_a_key(store, monkeypatch):
    """llm has NO loader-level guard, deliberately.

    `effective_api_key` delegates to `resolve_runtime_credential` for a real
    user, so the tier-1 raise fires before its tier-4 env fallback is reached.
    Its own two env reads are unguardable by construction (no connector / no
    user). This test is what proves tier 1 is doing that job.
    """
    from connectors.registry import LLM_ANTHROPIC

    from services import llm_connector_map

    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "env-key")
    _seed_and_disable(LLM_ANTHROPIC, {"api_key": "mine"})

    with pytest.raises(ccs.ConnectorNotConfigured):
        llm_connector_map.effective_api_key("alice", "ANTHROPIC_API_KEY")


def test_an_absent_llm_row_still_reaches_the_env_key(store, monkeypatch):
    from services import llm_connector_map

    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "env-key")

    assert llm_connector_map.effective_api_key("nobody", "ANTHROPIC_API_KEY") == "env-key"


def test_disabling_an_llm_connector_drops_the_cached_adapter(store, monkeypatch):
    """`set_enabled` must fire the change bus.

    It is shaped after `set_delivery_paused`, which fires nothing — so the
    obvious implementation would leave `config`'s per-user adapter cache
    holding a live key and chat working until the next restart. That would
    falsify "disabling stops actions" while every other test here passed.
    """
    from connectors.registry import LLM_ANTHROPIC

    fired: list[str] = []
    ccs.reset_listeners_for_tests()
    ccs.register_change_listener(
        lambda user_id, connector, action, instance="": fired.append(action)
    )
    try:
        ccs.put_payload("alice", LLM_ANTHROPIC, {"api_key": "k"}, actor="self")
        fired.clear()
        ccs.set_enabled("alice", LLM_ANTHROPIC, enabled=False, actor="self")
    finally:
        ccs.reset_listeners_for_tests()

    assert fired == ["enabled"], (
        "set_enabled fired no change event; the cached LLM adapter keeps the key"
    )
