# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Unit tests for services/email_credentials.py (LUM-170).

Payload validation, TLS enforcement, the dedicated IMAP SSRF host guard, and
multi-mailbox (instance-keyed) load / env-fallback behaviour.
"""

from __future__ import annotations

import pytest
from connectors.registry import IMAP

from services import connector_credentials as ccs
from services import email_credentials as emc

_GOOD = {
    "host": "imap.example.com",
    "port": 993,
    "username": "alice@example.com",
    "password": "app-pw",
    "use_ssl": True,
    "auth_type": "app_password",
}


@pytest.fixture(autouse=True)
def _public_host(monkeypatch):
    # Default: host resolves to a public IP (passes the SSRF guard).
    monkeypatch.setattr(emc._ohu, "_resolved_ips", lambda host: ["93.184.216.34"])
    monkeypatch.setenv("LUMOGIS_ALLOW_PRIVATE_OUTBOUND_URLS", "false")


def test_valid_payload_builds_connection():
    conn = emc._connection_from_payload(dict(_GOOD))
    assert conn.host == "imap.example.com"
    assert conn.port == 993
    assert conn.password == "app-pw"
    assert conn.auth_type == "app_password"


def test_non_dict_payload_raises_credential_unavailable():
    with pytest.raises(ccs.CredentialUnavailable):
        emc._connection_from_payload(["not", "a", "dict"])


def test_missing_host_raises_credential_unavailable():
    p = {k: v for k, v in _GOOD.items() if k != "host"}
    with pytest.raises(ccs.CredentialUnavailable):
        emc._connection_from_payload(p)


def test_empty_password_raises_value_error():
    p = dict(_GOOD, password="")
    with pytest.raises(ValueError):
        emc._connection_from_payload(p)


def test_bad_port_raises_value_error():
    with pytest.raises(ValueError):
        emc._connection_from_payload(dict(_GOOD, port=70000))


def test_unknown_auth_type_raises_value_error():
    with pytest.raises(ValueError):
        emc._connection_from_payload(dict(_GOOD, auth_type="wat"))


def test_plaintext_to_remote_host_rejected():
    with pytest.raises(ValueError, match="requires TLS"):
        emc._connection_from_payload(dict(_GOOD, use_ssl=False))


def test_plaintext_to_loopback_allowed(monkeypatch):
    # Self-hosted Dovecot on the same box: operator allows private/loopback.
    monkeypatch.setenv("LUMOGIS_ALLOW_PRIVATE_OUTBOUND_URLS", "true")
    monkeypatch.setattr(emc._ohu, "_resolved_ips", lambda host: ["127.0.0.1"])
    conn = emc._connection_from_payload(dict(_GOOD, host="localhost", use_ssl=False))
    assert conn.use_ssl is False


def test_imap_host_link_local_blocked(monkeypatch):
    # 169.254.169.254 (cloud metadata) must always be blocked.
    with pytest.raises(ValueError, match="blocked"):
        emc._connection_from_payload(dict(_GOOD, host="169.254.169.254"))


def test_imap_host_private_blocked_when_disallowed(monkeypatch):
    monkeypatch.setattr(emc._ohu, "_resolved_ips", lambda host: ["10.0.0.5"])
    with pytest.raises(ValueError, match="blocked"):
        emc._connection_from_payload(dict(_GOOD, host="internal.host"))


# --- LUM-782: connect with the IDNA A-label ---------------------------------


def _record_resolves(monkeypatch):
    seen: list[str] = []

    def _fake(host):
        seen.append(host)
        return ["93.184.216.34"]

    monkeypatch.setattr(emc._ohu, "_resolved_ips", _fake)
    return seen


def test_deviation_host_connects_as_idna2008_a_label(monkeypatch):
    """`faß.example` must not resolve/connect as the IDNA2003 `fass.example`."""
    seen = _record_resolves(monkeypatch)
    conn = emc._connection_from_payload(dict(_GOOD, host="faß.example"))
    assert conn.host == "xn--fa-hia.example"
    assert seen and set(seen) == {"xn--fa-hia.example"}
    assert not any("fass" in h or "ß" in h for h in seen)


def test_unicode_host_connects_with_a_label(monkeypatch):
    seen = _record_resolves(monkeypatch)
    conn = emc._connection_from_payload(dict(_GOOD, host="Bücher.Example."))
    assert conn.host == "xn--bcher-kva.example"
    assert set(seen) == {"xn--bcher-kva.example"}


def test_invalid_host_fails_closed_before_resolving(monkeypatch):
    seen = _record_resolves(monkeypatch)
    with pytest.raises(ValueError, match="not a valid hostname"):
        emc._connection_from_payload(dict(_GOOD, host="xn--zz.example"))
    assert seen == []


def test_smtp_host_is_normalised_and_invalid_rejected():
    assert emc._smtp_host_port({"smtp_host": "Faß.example."}, "x.example") == (
        "xn--fa-hia.example",
        587,
    )
    with pytest.raises(ValueError):
        emc._smtp_host_port({"smtp_host": "a..b"}, "x.example")


# --- multi-mailbox load / instance behaviour --------------------------------


def test_load_connection_uses_instance(monkeypatch):
    calls = {}

    def fake_get_payload(user_id, connector, *, instance=""):
        calls["args"] = (user_id, connector, instance)
        return dict(_GOOD)

    monkeypatch.setattr(ccs, "get_payload", fake_get_payload)
    conn = emc.load_connection("u1", instance="src-work")
    assert calls["args"] == ("u1", IMAP, "src-work")
    assert conn.username == "alice@example.com"


def test_load_connection_missing_row_raises_not_configured(monkeypatch):
    monkeypatch.setattr(ccs, "get_payload", lambda u, c, *, instance="": None)
    monkeypatch.setattr("services.email_credentials.auth_enabled", lambda: True)
    with pytest.raises(ccs.ConnectorNotConfigured):
        emc.load_connection("u1", instance="src-x")


def test_env_fallback_only_for_default_instance(monkeypatch):
    monkeypatch.setattr(ccs, "get_payload", lambda u, c, *, instance="": None)
    monkeypatch.setattr("services.email_credentials.auth_enabled", lambda: False)
    monkeypatch.setenv("EMAIL_IMAP_HOST", "imap.example.com")
    monkeypatch.setenv("EMAIL_IMAP_USERNAME", "alice@example.com")
    monkeypatch.setenv("EMAIL_IMAP_PASSWORD", "app-pw")
    # instance="" → env fallback used
    conn = emc.load_connection("default", instance="")
    assert conn.host == "imap.example.com"
    # a numbered mailbox has no env analogue → not configured
    with pytest.raises(ccs.ConnectorNotConfigured):
        emc.load_connection("default", instance="src-1")


# --- LUM-778: the legacy `instance=""` row, converged onto this accessor ------


def test_a_legacy_instance_row_answers_for_a_per_source_mailbox(monkeypatch):
    """The pre-LUM-170 row, which no write path can recreate.

    Migration 054 added `instance` with `DEFAULT ''`, so a mailbox configured
    before multi-instance shipped still sits at `instance=""` while its
    `sources.id` asks for `instance="src-1"`. Nothing will ever write another
    one — `_validate_instance(..., write=True)` requires a non-empty instance
    for a multi-instance connector — so an explicit read is the only way that
    deployment ever resolves a username.

    This fallback used to live in `paperless_overlap._mailbox_username` as a
    second implementation of this function (LUM-778).
    """
    seen: list = []

    def _payload(_u, _c, *, instance=""):
        seen.append(instance)
        return {"username": "alice@example.com"} if instance == "" else None

    monkeypatch.setattr(ccs, "get_payload", _payload)
    monkeypatch.setattr("services.email_credentials.auth_enabled", lambda: False)
    assert emc.mailbox_account_address("default", instance="src-1") == "alice@example.com"
    assert seen == ["src-1", ""], "the mailbox's own row must be tried first"


def test_the_legacy_row_is_not_consulted_when_auth_is_on(monkeypatch):
    """With AUTH on, `instance=""` is another mailbox's row, not this one's.

    Answering "what address is mailbox X?" out of mailbox Y's credential is
    wrong in a way that is invisible — both are the same user's real addresses.
    The unguarded version of this fallback did exactly that, and fed the result
    to paperless overlap's `high`/`low` decision, where `high` gates.
    """
    seen: list = []

    def _payload(_u, _c, *, instance=""):
        seen.append(instance)
        return {"username": "bob@example.com"} if instance == "" else None

    monkeypatch.setattr(ccs, "get_payload", _payload)
    monkeypatch.setattr("services.email_credentials.auth_enabled", lambda: True)
    with pytest.raises(ccs.ConnectorNotConfigured):
        emc.mailbox_account_address("u1", instance="src-1")
    assert seen == ["src-1"]


def test_the_mailboxs_own_row_wins_over_the_legacy_row(monkeypatch):
    """Ordering, pinned: the fallback must never shadow a real row.

    Both rows can coexist — migration 054 left the old one in place and the user
    then added mailboxes. Reading the legacy row first would resolve every
    mailbox to the same stale address.
    """

    def _payload(_u, _c, *, instance=""):
        return {"username": "new@example.com" if instance == "src-1" else "old@example.com"}

    monkeypatch.setattr(ccs, "get_payload", _payload)
    monkeypatch.setattr("services.email_credentials.auth_enabled", lambda: False)
    assert emc.mailbox_account_address("default", instance="src-1") == "new@example.com"


def test_an_absent_row_still_raises_rather_than_returning_none(monkeypatch):
    """The raise is the contract (ADR-214), and callers depend on it.

    `sending_addresses` omits the mailbox, `email_send` degrades to an
    unfiltered Cc, `paperless_overlap` skips it. Softening this to `None` would
    make all three read a missing address as an empty one.
    """
    monkeypatch.setattr(ccs, "get_payload", lambda u, c, *, instance="": None)
    monkeypatch.setattr("services.email_credentials.auth_enabled", lambda: False)
    monkeypatch.delenv("EMAIL_IMAP_USERNAME", raising=False)
    with pytest.raises(ccs.ConnectorNotConfigured):
        emc.mailbox_account_address("default", instance="src-1")


def test_list_mailboxes_returns_imap_instances(monkeypatch):
    from services.connector_credentials import CredentialRecord

    def rec(connector, instance):
        return CredentialRecord(
            user_id="u1",
            connector=connector,
            created_at=None,
            updated_at=None,
            created_by="self",
            updated_by="self",
            key_version=1,
            instance=instance,
        )

    monkeypatch.setattr(
        ccs,
        "list_records",
        lambda user_id: [rec("imap", "src-a"), rec("imap", "src-b"), rec("caldav", "")],
    )
    assert sorted(emc.list_mailboxes("u1")) == ["src-a", "src-b"]


# --- xoauth2 (LUM-686 / 674a) ------------------------------------------------


_XOAUTH2 = {
    "host": "imap.example.com",
    "port": 993,
    "username": "alice@example.com",
    "use_ssl": True,
    "auth_type": "xoauth2",
    "provider": "google",
    "refresh_token": "rt-abc",
}


def test_xoauth2_accepted_at_load():
    # Replaces the old test_xoauth2_rejected_in_v1: xoauth2 is now accepted at
    # load once the caller has minted an access token.
    conn = emc._connection_from_payload(dict(_XOAUTH2), access_token="ACCESS")
    assert conn.auth_type == "xoauth2"
    assert conn.oauth_access_token == "ACCESS"
    assert conn.password is None


def test_xoauth2_connection_without_token_raises_value_error():
    # access_token defaults None → programming error (loader must mint first).
    with pytest.raises(ValueError):
        emc._connection_from_payload(dict(_XOAUTH2))


def test_xoauth2_imap_row_mints_token(monkeypatch):
    from services import email_oauth

    seen = {}

    def fake_token(user_id, *, connector, instance, payload):
        seen["args"] = (user_id, connector, instance)
        return "ACCESS-TOKEN"

    monkeypatch.setattr(ccs, "get_payload", lambda u, c, *, instance="": dict(_XOAUTH2))
    monkeypatch.setattr(email_oauth, "get_access_token", fake_token)
    conn = emc.load_connection("u1", instance="src-1")
    assert conn.auth_type == "xoauth2"
    assert conn.oauth_access_token == "ACCESS-TOKEN"
    assert conn.password is None
    assert seen["args"] == ("u1", IMAP, "src-1")


def test_xoauth2_missing_refresh_token_raises_credential_unavailable(monkeypatch):
    # Exercised through the REAL load_connection (which calls the real
    # get_access_token first); the raised type must be a CredentialUnavailable
    # (satisfied by OAuthRefreshError ⊂ CredentialUnavailable).
    bad = {k: v for k, v in _XOAUTH2.items() if k != "refresh_token"}
    monkeypatch.setattr(ccs, "get_payload", lambda u, c, *, instance="": dict(bad))
    with pytest.raises(ccs.CredentialUnavailable):
        emc.load_connection("u1", instance="src-1")


def test_xoauth2_smtp_reuses_imap_token(monkeypatch):
    from services import email_oauth

    mint_calls = {"n": 0}

    def fake_token(user_id, *, connector, instance, payload):
        mint_calls["n"] += 1
        return "ACCESS-TOKEN"

    def fake_get_payload(u, c, *, instance=""):
        if c == IMAP:
            return dict(_XOAUTH2)
        return {"smtp_host": "smtp.example.com", "smtp_port": 587}

    monkeypatch.setattr(ccs, "get_payload", fake_get_payload)
    monkeypatch.setattr(email_oauth, "get_access_token", fake_token)
    conn = emc.load_smtp_connection("u1", instance="src-1")
    assert conn.auth_type == "xoauth2"
    assert conn.oauth_access_token == "ACCESS-TOKEN"
    assert conn.password == ""  # unused sentinel
    assert conn.host == "smtp.example.com"
    assert conn.username == "alice@example.com"
    # Minted exactly once (by load_connection), NOT a second time for SMTP.
    assert mint_calls["n"] == 1


def test_xoauth2_smtp_ignores_standalone_app_password(monkeypatch):
    from services import email_oauth

    def fake_get_payload(u, c, *, instance=""):
        if c == IMAP:
            return dict(_XOAUTH2)
        # An explicit standalone SMTP app-password must be IGNORED for xoauth2.
        return {
            "smtp_host": "smtp.example.com",
            "smtp_port": 587,
            "reuse_imap": False,
            "app_password": "STANDALONE-PW",
        }

    monkeypatch.setattr(ccs, "get_payload", fake_get_payload)
    monkeypatch.setattr(
        email_oauth,
        "get_access_token",
        lambda user_id, *, connector, instance, payload: "ACCESS-TOKEN",
    )
    conn = emc.load_smtp_connection("u1", instance="src-1")
    assert conn.auth_type == "xoauth2"
    assert conn.oauth_access_token == "ACCESS-TOKEN"
    assert conn.password == ""  # NOT "STANDALONE-PW"


def test_xoauth2_smtp_none_token_raises_oauth_refresh_error(monkeypatch):
    from services import email_oauth

    def fake_get_payload(u, c, *, instance=""):
        if c == IMAP:
            return dict(_XOAUTH2)
        return {"smtp_host": "smtp.example.com", "smtp_port": 587}

    monkeypatch.setattr(ccs, "get_payload", fake_get_payload)
    # Force the programming-error state: an xoauth2 IMAP conn with no token.
    monkeypatch.setattr(
        emc,
        "load_connection",
        lambda u, *, instance="": emc.ImapConnection(
            host="imap.example.com",
            port=993,
            username="alice@example.com",
            use_ssl=True,
            auth_type="xoauth2",
            password=None,
            oauth_access_token=None,
        ),
    )
    with pytest.raises(email_oauth.OAuthRefreshError):
        emc.load_smtp_connection("u1", instance="src-1")


def test_app_password_smtp_reuse_unchanged(monkeypatch):
    # Regression: the app-password SMTP path is byte-for-byte unchanged.
    def fake_get_payload(u, c, *, instance=""):
        if c == IMAP:
            return dict(_GOOD)
        return {"smtp_host": "smtp.example.com", "smtp_port": 587}  # reuse_imap defaults True

    monkeypatch.setattr(ccs, "get_payload", fake_get_payload)
    conn = emc.load_smtp_connection("u1", instance="src-1")
    assert conn.auth_type == "app_password"
    assert conn.password == "app-pw"  # reused from IMAP
    assert conn.oauth_access_token is None
