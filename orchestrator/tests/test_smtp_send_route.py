# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Route-logic tests for PUT /me/email-sources/{id}/smtp (LUM-650).

Calls the route function directly with a fake user + monkeypatched credential /
transport seams (the ASGI auth/CSRF/rate-limit dependencies are standard and
covered elsewhere). Covers: owner-scope 404, disable removes the row, enable
stores + live-tests, an auth failure does NOT persist, and an "Other" mailbox
without a submission host is a 422.
"""

from __future__ import annotations

import logging

import pytest
from actions.errors import PermanentActionError
from auth import UserContext
from fastapi import HTTPException
from models.email_setup import EmailSmtpUpdate
from routes import signals as sroutes

import config

_UID = "default"
_SID = "11111111-1111-1111-1111-111111111111"


class _Store:
    def __init__(self, owned=True):
        self.owned = owned

    def fetch_one(self, q, params=None):
        ql = " ".join((q or "").split()).lower()
        if "select id::text as id from sources" in ql:
            return {"id": _SID} if self.owned else None
        return None

    def execute(self, q, params=None):
        pass


class _Imap:
    host = "imap.gmail.com"
    username = "alice@gmail.com"
    password = "imap-pw"
    # LUM-771 — the real `ImapConnection` carries these; a fake missing them let
    # the route's OAuth path go untested for two chunks. Deliberately NOT repaired
    # in the route with `getattr(imap, "auth_type", "app_password")`: that default
    # is the silent fallback the fix exists to remove, and it would have hidden
    # this gap rather than closed it.
    auth_type = "app_password"
    oauth_access_token = None


@pytest.fixture
def env(monkeypatch):
    store = _Store()
    monkeypatch.setattr(config, "get_metadata_store", lambda: store)
    # LUM-759: this was `._hits.clear() if hasattr(…, "_hits") else None`, which
    # was a NO-OP — `RequestRateLimiter` stores buckets in `_store` and exposes a
    # public `clear()`. The guard failed open, so the limiter was never reset; it
    # went unnoticed only because this file had 7 tests and the limit is 10/60s.
    # Adding the LUM-759 cases pushed it over and every later test 429'd.
    sroutes._email_smtp_limiter.clear()
    from services import email_credentials

    monkeypatch.setattr(email_credentials, "load_connection", lambda uid, *, instance: _Imap())
    return store


def _user():
    return UserContext(user_id=_UID, is_authenticated=True, role="admin")


def test_owner_scope_404(env, monkeypatch):
    env.owned = False
    with pytest.raises(HTTPException) as ei:
        sroutes.set_email_smtp(_SID, EmailSmtpUpdate(enabled=True), user=_user())
    assert ei.value.status_code == 404


def test_disable_removes_row(env, monkeypatch):
    calls = {}
    from services import connector_credentials as ccs

    def _del(uid, connector, *, actor, instance):
        calls["deleted"] = (connector, instance)

    monkeypatch.setattr(ccs, "delete_payload", _del)
    res = sroutes.set_email_smtp(_SID, EmailSmtpUpdate(enabled=False), user=_user())
    assert res.enabled is False and res.ok is True
    assert calls["deleted"] == ("smtp", _SID)


def test_enable_stores_and_tests(env, monkeypatch):
    stored = {}
    from adapters.email_smtp_sender import SmtpSender

    from services import connector_credentials as ccs

    monkeypatch.setattr(
        ccs,
        "put_payload",
        lambda uid, connector, payload, *, actor, instance: stored.update(payload) or None,
    )
    monkeypatch.setattr(SmtpSender, "verify", lambda self, **kw: None)
    res = sroutes.set_email_smtp(_SID, EmailSmtpUpdate(enabled=True), user=_user())
    assert res.ok and res.enabled and res.tested
    assert stored == {"reuse_imap": True}  # Gmail → host/port from registry, not stored


def test_enable_auth_failure_does_not_persist(env, monkeypatch):
    stored = {"called": False}
    from adapters.email_smtp_sender import SmtpSender

    from services import connector_credentials as ccs

    monkeypatch.setattr(
        ccs,
        "put_payload",
        lambda *a, **k: stored.update(called=True),
    )

    def _bad(self, **kw):
        raise PermanentActionError("smtp_auth", "bad")

    monkeypatch.setattr(SmtpSender, "verify", _bad)
    res = sroutes.set_email_smtp(_SID, EmailSmtpUpdate(enabled=True), user=_user())
    assert res.ok is False and res.enabled is False and res.error == "smtp_auth"
    assert stored["called"] is False  # never persisted a broken opt-in


# LUM-694 — the credential store validates ``actor`` against ``self|system|
# admin:<id>`` (``_actor_str``); a raw ``user_id`` raises ``ValueError`` → the
# whole opt-in path 500s. The pre-existing tests above monkeypatch the store with
# fakes that ignore ``actor``, so they never exercised that validation. These two
# run the captured actor through the REAL validator — they fail if the route ever
# reverts to ``actor=user.user_id``.
def test_disable_uses_a_store_valid_actor(env, monkeypatch):
    from services import connector_credentials as ccs

    captured = {}
    monkeypatch.setattr(
        ccs,
        "delete_payload",
        lambda uid, connector, *, actor, instance: captured.update(actor=actor),
    )
    sroutes.set_email_smtp(_SID, EmailSmtpUpdate(enabled=False), user=_user())
    assert captured["actor"] == "self"
    # The REAL validator accepts it (would raise on a raw user_id like "default").
    assert ccs._actor_str(captured["actor"]) == "self"
    with pytest.raises(ValueError):
        ccs._actor_str(_UID)  # proves user_id would have been rejected


def test_enable_uses_a_store_valid_actor(env, monkeypatch):
    from adapters.email_smtp_sender import SmtpSender

    from services import connector_credentials as ccs

    captured = {}
    monkeypatch.setattr(
        ccs,
        "put_payload",
        lambda uid, connector, payload, *, actor, instance: captured.update(actor=actor),
    )
    monkeypatch.setattr(SmtpSender, "verify", lambda self, **kw: None)
    res = sroutes.set_email_smtp(_SID, EmailSmtpUpdate(enabled=True), user=_user())
    assert res.ok and res.enabled
    assert captured["actor"] == "self"
    assert ccs._actor_str(captured["actor"]) == "self"  # real validation passes


def test_other_provider_requires_host(env, monkeypatch):
    from services import email_credentials

    # An "Other" mailbox (unknown IMAP host → no provider registry entry).
    class _OtherImap:
        host = "imap.unknown.example"
        username = "me@unknown.example"
        password = "pw"
        auth_type = "app_password"
        oauth_access_token = None

    monkeypatch.setattr(email_credentials, "load_connection", lambda uid, *, instance: _OtherImap())
    with pytest.raises(HTTPException) as ei:
        sroutes.set_email_smtp(
            _SID, EmailSmtpUpdate(enabled=True, reuse_imap_password=True), user=_user()
        )
    assert ei.value.status_code == 422
    assert ei.value.detail["code"] == "smtp_host_required"


# --- LUM-759: the IMAP-load failure surface ---------------------------------- #
#
# `load_connection` documents four raise types and, since LUM-686, makes an
# outbound token-refresh POST for an xoauth2 mailbox. The route used to wrap it
# in `except Exception` → 422 imap_not_configured `from None`. Every case below
# was reported as "your mailbox isn't configured" before this chunk, and NONE of
# them had a test — the `env` fixture above monkeypatches `load_connection` to
# always succeed, so no test in this file entered the except at all.


def _raises(monkeypatch, exc: BaseException):
    """Point the route's IMAP load at a specific failure."""
    from services import email_credentials

    def _boom(uid, *, instance):
        raise exc

    monkeypatch.setattr(email_credentials, "load_connection", _boom)


def _optin(status_expected: int):
    with pytest.raises(HTTPException) as ei:
        sroutes.set_email_smtp(_SID, EmailSmtpUpdate(enabled=True), user=_user())
    assert ei.value.status_code == status_expected
    return ei.value


def test_no_credential_row_is_still_imap_not_configured(env, monkeypatch):
    """The one case the old blanket catch got right, kept right."""
    from services import connector_credentials as ccs

    _raises(monkeypatch, ccs.ConnectorNotConfigured("no imap row"))
    assert _optin(422).detail["code"] == "imap_not_configured"


def test_a_revoked_grant_asks_for_a_reconnect(env, monkeypatch):
    """Distinct from `imap_not_configured`: the mailbox IS set up, the grant died.

    One code for both would tell a user to "add your mailbox" when the fix is
    "reconnect it" — the collapse this lane exists to undo (LUM-757).
    """
    from services.email_oauth import OAuthRefreshError

    _raises(monkeypatch, OAuthRefreshError("token refresh failed (400) for microsoft"))
    assert _optin(422).detail["code"] == "imap_reauth_required"


def test_an_unreachable_token_endpoint_is_a_503(env, monkeypatch):
    """The ticket's own case. A 4xx asserts the client must change the request;
    a token endpoint that timed out is not something the client can change."""
    from services.email_oauth import OAuthRefreshError

    _raises(monkeypatch, OAuthRefreshError("boom", kind="network"))
    assert _optin(503).detail["code"] == "mailbox_unreachable"


def test_an_egress_block_names_the_operator(env, monkeypatch):
    from services.email_oauth import OAuthRefreshError

    _raises(monkeypatch, OAuthRefreshError("boom", kind="egress_blocked"))
    assert _optin(503).detail["code"] == "egress_blocked"


def test_a_missing_client_id_is_an_operator_problem_not_a_settings_one(env, monkeypatch):
    """`config` means two different things and this is the half the table can't hold.

    ADR 195 calls both "an operator never set LUMOGIS_OAUTH_*_CLIENT_ID" and "the
    user typed a bad port" `config`. Routing this through `_SMTP_OPTIN_HTTP` would
    answer 422 imap_settings_invalid — telling a household member to correct
    settings they cannot see, to fix an environment variable they cannot set.
    """
    from services.email_oauth import OAuthRefreshError

    _raises(monkeypatch, OAuthRefreshError("no client id", kind="config"))
    exc = _optin(503)
    assert exc.detail["code"] == "oauth_unavailable"  # NOT imap_settings_invalid
    # And it reuses the code `_resolve_oauth_provider` already answers this exact
    # condition with, twelve lines below — rather than minting a synonym.
    assert sroutes._SMTP_OPTIN_HTTP["config"] == (422, "imap_settings_invalid")


def test_a_blocked_host_and_an_unresolved_host_are_told_apart(env, monkeypatch):
    """LUM-633 split these two out of a bare ValueError precisely so they would
    stop telling the user the same story. The extraction must not re-merge them."""
    from services import email_credentials

    _raises(monkeypatch, email_credentials.ImapHostBlockedError("policy"))
    assert _optin(503).detail["code"] == "egress_blocked"

    _raises(monkeypatch, email_credentials.ImapHostUnresolvedError("dns"))
    assert _optin(503).detail["code"] == "mailbox_unreachable"


def test_a_bad_stored_port_is_a_settings_problem(env, monkeypatch):
    _raises(monkeypatch, ValueError("imap payload 'port' must be an int in 1..65535"))
    assert _optin(422).detail["code"] == "imap_settings_invalid"


def test_a_decrypt_failure_is_not_reported_as_not_configured(env, monkeypatch):
    from services import connector_credentials as ccs

    _raises(monkeypatch, ccs.CredentialUnavailable("decrypt failed"))
    assert _optin(503).detail["code"] == "credential_unavailable"


def test_a_decrypt_failure_does_not_ask_the_user_to_reconnect(env, monkeypatch):
    """Pins the P1 self-review found in this chunk's own plan.

    An earlier draft caught `(CredentialUnavailable, ValueError)` together and ran
    both through `classify_credential_failure`, whose DEFAULT is `credential` →
    422 imap_reauth_required. A broken encryption key would have told the user to
    reconnect a mailbox whose credentials are fine. Every other test in this file
    passes under that mutation.
    """
    from services import connector_credentials as ccs

    _raises(monkeypatch, ccs.CredentialUnavailable("decrypt failed"))
    exc = _optin(503)
    assert exc.detail["code"] != "imap_reauth_required"


def test_the_oauth_clause_precedes_the_credential_clause(env, monkeypatch):
    """`OAuthRefreshError` ⊂ `CredentialUnavailable`, so reversing the two clauses
    makes the first unreachable — and it fails SILENTLY: every OAuth failure would
    answer a plausible-looking 503 credential_unavailable. No test that only
    checked "is it a 5xx" would notice."""
    from services.email_oauth import OAuthRefreshError

    _raises(monkeypatch, OAuthRefreshError("boom", kind="network"))
    assert _optin(503).detail["code"] == "mailbox_unreachable"  # not credential_unavailable


def test_an_unexpected_error_is_not_swallowed_into_a_4xx(env, monkeypatch):
    """A bug in Lumogis must reach FastAPI as a 500 with a traceback. As a 422 it
    is invisible to alerting AND blames the user's settings."""
    _raises(monkeypatch, AttributeError("'NoneType' object has no attribute 'host'"))
    with pytest.raises(AttributeError):
        sroutes.set_email_smtp(_SID, EmailSmtpUpdate(enabled=True), user=_user())


@pytest.mark.parametrize(
    "exc_factory",
    [
        lambda: __import__("services.connector_credentials", fromlist=["x"]).ConnectorNotConfigured(
            "SEKRIT-host.internal"
        ),
        lambda: __import__("services.email_oauth", fromlist=["x"]).OAuthRefreshError(
            "SEKRIT-host.internal", kind="network"
        ),
        lambda: __import__("services.connector_credentials", fromlist=["x"]).CredentialUnavailable(
            "SEKRIT-host.internal"
        ),
        lambda: ValueError("SEKRIT-host.internal"),
    ],
)
def test_no_branch_leaks_the_exception_message(env, monkeypatch, exc_factory):
    """The credential layer's messages name hosts, payload fields and user_ids —
    and since LUM-759 this response is RENDERED by the setup form."""
    _raises(monkeypatch, exc_factory())
    with pytest.raises(HTTPException) as ei:
        sroutes.set_email_smtp(_SID, EmailSmtpUpdate(enabled=True), user=_user())
    rendered = repr(ei.value.detail) + str(ei.value.detail)
    assert "SEKRIT" not in rendered


def test_every_failure_is_logged(env, monkeypatch, caplog):
    """The old block had no log call at all, so an operator debugging a failed
    opt-in had one 422 and nothing else — not even the exception type."""
    from services import connector_credentials as ccs

    _raises(monkeypatch, ccs.CredentialUnavailable("decrypt failed"))
    with caplog.at_level(logging.WARNING, logger="routes.signals"):
        with pytest.raises(HTTPException):
            sroutes.set_email_smtp(_SID, EmailSmtpUpdate(enabled=True), user=_user())
    msgs = [r.getMessage() for r in caplog.records]
    assert any("email.smtp.optin.imap_load_failed" in m for m in msgs)
    assert any("CredentialUnavailable" in m for m in msgs)
    assert not any("decrypt failed" in m for m in msgs), "the message must not reach the log"


def test_the_status_table_covers_exactly_the_health_codes():
    """The test that earns the extraction.

    Critique R1 proposed dropping the shared classifier for typed `except
    ImapHost*Error` clauses — same behaviour, less machinery. The two designs
    differ only in how they fail when ADR 195 gains a fifth code: typed clauses
    let a new ValueError subclass fall through to `imap_settings_invalid`
    (silently wrong, on the surface whose purpose is not being silently wrong),
    while the shared classifier returns a code this table does not have.

    A runtime KeyError is a poor way to be loud — it needs that branch to execute
    in production. This makes it fail at edit time instead.
    """
    from services.email_oauth import _KINDS

    assert set(sroutes._SMTP_OPTIN_HTTP) == _KINDS


# --- LUM-771: the OAuth enable path --------------------------------------- #
#
# `verify_smtp_connection` has accepted `auth_type`/`access_token` since LUM-693
# and the OAuth device-flow route ten lines below passes them; THIS route did not.
# An xoauth2 `ImapConnection` carries `password = None` by design, so the pre-save
# connect-test authenticated with an empty app password and failed every time —
# and because the device flow auto-enables SMTP at connect, disabling sending on
# an Outlook mailbox was a one-way door. None of the 24 cases above caught it:
# every one of them uses an app-password fake.


class _OAuthImap:
    """An Outlook mailbox connected via the device flow (LUM-687)."""

    host = "outlook.office365.com"
    username = "alice@outlook.com"
    password = None  # `email_credentials` sets this deliberately for xoauth2
    auth_type = "xoauth2"
    oauth_access_token = "ya29.access-token"


def _capture_verify(monkeypatch) -> dict:
    """Capture what reaches the ADAPTER, not the service seam — so the assertion
    covers the whole route → smtp_transport → SmtpSender chain."""
    from adapters.email_smtp_sender import SmtpSender

    captured: dict = {}
    monkeypatch.setattr(SmtpSender, "verify", lambda self, **kw: captured.update(kw))
    return captured


def _use(monkeypatch, imap_cls) -> None:
    from services import email_credentials

    monkeypatch.setattr(email_credentials, "load_connection", lambda uid, *, instance: imap_cls())


def _allow_persist(monkeypatch) -> None:
    from services import connector_credentials as ccs

    monkeypatch.setattr(ccs, "put_payload", lambda *a, **k: None)


def test_an_oauth_mailbox_verifies_with_its_token_not_an_empty_password(env, monkeypatch):
    """The test whose absence let this defect exist: LUM-759 covered this route's
    OAuth ERROR branches exhaustively and never once ran its happy path."""
    _use(monkeypatch, _OAuthImap)
    _allow_persist(monkeypatch)
    captured = _capture_verify(monkeypatch)

    res = sroutes.set_email_smtp(_SID, EmailSmtpUpdate(enabled=True), user=_user())

    assert res.ok and res.enabled and res.tested
    assert captured["auth_type"] == "xoauth2"
    assert captured["access_token"] == "ya29.access-token"
    # And the registry still supplies the submission host — an OAuth mailbox is
    # not an "Other" mailbox.
    assert (captured["host"], captured["port"]) == ("smtp.office365.com", 587)


def test_a_stale_token_on_an_app_password_mailbox_is_not_treated_as_oauth(env, monkeypatch):
    """The ONLY test that kills a token-presence discriminator.

    Test 1 alone cannot: its fixture sets `auth_type="xoauth2"` AND a token, so
    `getattr(imap, "oauth_access_token", None) is not None` — the check Decision 1
    rejects — passes it. Here the two disagree: an app-password mailbox that still
    carries a token from a previous connection. `auth_type` is the fact; the token
    is an artefact that can outlive it.
    """

    class _StaleTokenImap(_Imap):
        auth_type = "app_password"
        oauth_access_token = "ya29.left-over-from-a-previous-connection"

    _use(monkeypatch, _StaleTokenImap)
    _allow_persist(monkeypatch)
    captured = _capture_verify(monkeypatch)

    sroutes.set_email_smtp(_SID, EmailSmtpUpdate(enabled=True), user=_user())

    assert captured["auth_type"] == "app_password"
    assert captured["access_token"] is None  # the stale token must NOT be offered
    assert captured["app_password"] == "imap-pw"


def test_a_connect_failure_on_an_oauth_mailbox_stays_smtp_connect(env, monkeypatch):
    """An over-broad remap (`if is_oauth and error_code:`) passes every other test
    here while telling a user their provider forbids sending when the server was
    merely unreachable — a wrong answer that costs them a support conversation."""
    from adapters.email_smtp_sender import SmtpSender

    _use(monkeypatch, _OAuthImap)

    def _unreachable(self, **kw):
        raise PermanentActionError("smtp_connect", "connection refused")

    monkeypatch.setattr(SmtpSender, "verify", _unreachable)

    res = sroutes.set_email_smtp(_SID, EmailSmtpUpdate(enabled=True), user=_user())
    assert res.ok is False and res.error == "smtp_connect"


def test_a_tenant_denied_oauth_send_is_not_reported_as_a_bad_password(env, monkeypatch):
    """Both halves, so the narrowing cannot be over-applied.

    A Microsoft 365 tenant with SMTP AUTH switched off — or a grant missing the
    send scope — refuses an OAuth mailbox with `smtp_auth`. There is no password
    to correct, so a client keying on `smtp_auth` would offer a password field to
    a user who cannot use one: the exact "sent hunting for a wrong answer" harm.
    """
    from adapters.email_smtp_sender import SmtpSender

    def _denied(self, **kw):
        raise PermanentActionError("smtp_auth", "535 5.7.139 authentication unsuccessful")

    monkeypatch.setattr(SmtpSender, "verify", _denied)

    _use(monkeypatch, _OAuthImap)
    res = sroutes.set_email_smtp(_SID, EmailSmtpUpdate(enabled=True), user=_user())
    assert res.ok is False and res.enabled is False and res.error == "smtp_oauth_denied"

    # …and an app-password mailbox still gets the code that DOES name its fix.
    _use(monkeypatch, _Imap)
    res = sroutes.set_email_smtp(_SID, EmailSmtpUpdate(enabled=True), user=_user())
    assert res.error == "smtp_auth"


def test_disabling_returns_enabled_false_not_a_soft_save(env, monkeypatch):
    """Pins the one field that tells the two apart.

    Disable answers `{ok:true, enabled:false, tested:false}`; an egress-blocked
    enable answers `{ok:true, enabled:true, tested:false}`. A client rendering
    from `(ok, tested)` alone shows "on — not yet verified" for a mailbox whose
    sending it just switched off.
    """
    from adapters.email_smtp_sender import SmtpSender

    from services import connector_credentials as ccs

    monkeypatch.setattr(ccs, "delete_payload", lambda *a, **k: None)
    off = sroutes.set_email_smtp(_SID, EmailSmtpUpdate(enabled=False), user=_user())
    assert (off.ok, off.enabled, off.tested, off.error) == (True, False, False, None)

    def _blocked(self, **kw):
        raise PermanentActionError("egress_blocked", "containment")

    monkeypatch.setattr(SmtpSender, "verify", _blocked)
    _allow_persist(monkeypatch)
    soft = sroutes.set_email_smtp(_SID, EmailSmtpUpdate(enabled=True), user=_user())
    # Identical but for `enabled` — and `error` is present-and-null, not absent.
    assert (soft.ok, soft.enabled, soft.tested, soft.error) == (True, True, False, None)


def test_an_oauth_mailbox_refuses_a_standalone_sending_password(env, monkeypatch):
    """LUM-772 — an xoauth2 mailbox has no separate submission password.

    `load_smtp_connection` returns early with the OAuth token and **ignores** any
    stored `app_password` for one of these, so accepting one persisted an encrypted
    secret nothing would ever read and no revocation flow would ever clear.

    Refused, not silently dropped: discarding a password the user typed without
    saying so is the same "wrong cause, quietly" failure this route's whole lane
    exists to remove. The app-password mailbox's behaviour is unchanged, asserted
    in the same test so the refusal cannot be over-applied.
    """
    from adapters.email_smtp_sender import SmtpSender

    from services import connector_credentials as ccs

    stored = {"called": False}
    monkeypatch.setattr(ccs, "put_payload", lambda *a, **k: stored.update(called=True))
    monkeypatch.setattr(SmtpSender, "verify", lambda self, **kw: None)

    _use(monkeypatch, _OAuthImap)
    with pytest.raises(HTTPException) as ei:
        sroutes.set_email_smtp(
            _SID,
            EmailSmtpUpdate(enabled=True, reuse_imap_password=False, app_password="hunter2"),
            user=_user(),
        )
    assert ei.value.status_code == 422
    assert ei.value.detail["code"] == "smtp_password_not_applicable"
    assert stored["called"] is False, "the password must never reach the credential store"
    # Not `app_password_required` — that code means "you owe us one", the opposite.
    assert ei.value.detail["code"] != "app_password_required"

    # …and an app-password mailbox still accepts its own standalone password.
    _use(monkeypatch, _Imap)
    res = sroutes.set_email_smtp(
        _SID,
        EmailSmtpUpdate(enabled=True, reuse_imap_password=False, app_password="hunter2"),
        user=_user(),
    )
    assert res.ok and res.enabled


def test_an_oauth_mailbox_still_enables_on_the_reuse_path(env, monkeypatch):
    """The refusal above must not touch the path an OAuth mailbox actually uses.

    `reuse_imap_password=True` is the default and the only shape the device flow
    produces; a refusal keyed on `is_oauth` alone rather than on `is_oauth and not
    reuse` would break the very case LUM-771 just fixed.
    """
    _use(monkeypatch, _OAuthImap)
    _allow_persist(monkeypatch)
    captured = _capture_verify(monkeypatch)

    res = sroutes.set_email_smtp(
        _SID, EmailSmtpUpdate(enabled=True, reuse_imap_password=True), user=_user()
    )
    assert res.ok and res.enabled and res.tested
    assert captured["auth_type"] == "xoauth2"
