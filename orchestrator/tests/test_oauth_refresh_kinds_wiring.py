# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""The kind survives from the raise site to the surfaces that consume it (LUM-757).

`test_oauth_refresh_kinds.py` proves the mapping in isolation. This proves the
mapping is actually *reached* — through `get_access_token`'s real refresh branches,
into `_classify_construction_failure` (mailbox health) and `smtp_send`'s error
code. A mapping nothing consumes would pass the sibling file and change nothing.
"""

from __future__ import annotations

import httpx
import pytest
from services.email_oauth import OAuthRefreshError


@pytest.fixture
def token_endpoint(monkeypatch):
    """Route the token-refresh `httpx.Client` through a mock transport."""

    def _install(handler):
        original = httpx.Client

        def _factory(*args, **kwargs):
            kwargs.setdefault("transport", httpx.MockTransport(handler))
            return original(*args, **kwargs)

        monkeypatch.setattr("httpx.Client", _factory)

    return _install


_PAYLOAD = {"refresh_token": "rt", "provider": "microsoft"}


def _refresh(monkeypatch):
    """Drive the real refresh through its public entry point.

    `get_access_token` short-circuits on a live cached token, so the payload
    deliberately carries none — that forces the refresh branch where every raise
    site lives.
    """
    from services import email_oauth as mod

    monkeypatch.setattr(mod, "_client_id", lambda _p: "cid")
    return mod


def _call(mod):
    return mod.get_access_token("u1", connector="imap", instance="src-1", payload=dict(_PAYLOAD))


# --- raise sites: the kind reaches the exception ----------------------------- #


def test_a_transport_failure_is_network(token_endpoint, monkeypatch):
    from services import email_oauth as mod

    token_endpoint(lambda _r: (_ for _ in ()).throw(httpx.ConnectError("refused")))
    _refresh(monkeypatch)

    with pytest.raises(OAuthRefreshError) as ei:
        _call(mod)
    assert ei.value.kind == "network"


def test_an_egress_block_is_egress_blocked_not_network(token_endpoint, monkeypatch):
    """The finding both critics made independently.

    An egress block never clears without an operator editing the allowlist and
    restarting Core, so `network` copy ("usually clears up on its own") would be
    actively wrong — the ticket's own harm, reproduced inside its fix.
    """
    from services.egress_guard import EgressBlockedError

    from services import email_oauth as mod

    def _blocked(*_a, **_k):
        # Keyword-only constructor. A positional arg raises TypeError, which the
        # blanket `except Exception` swallows into `network` — so this test would
        # have failed for the wrong reason and, written slightly differently,
        # could have passed against a broken implementation.
        raise EgressBlockedError(host="login.microsoftonline.com", port=443)

    monkeypatch.setattr(mod, "oauth_egress_scope", _blocked)
    _refresh(monkeypatch)

    with pytest.raises(OAuthRefreshError) as ei:
        _call(mod)
    assert ei.value.kind == "egress_blocked", (
        "an egress block must not be folded into `network` by the blanket handler"
    )


def test_a_revoked_grant_is_credential(token_endpoint, monkeypatch):
    from services import email_oauth as mod

    token_endpoint(lambda _r: httpx.Response(400, json={"error": "invalid_grant"}))
    _refresh(monkeypatch)

    with pytest.raises(OAuthRefreshError) as ei:
        _call(mod)
    assert ei.value.kind == "credential"


def test_a_rate_limit_is_network(token_endpoint, monkeypatch):
    from services import email_oauth as mod

    token_endpoint(lambda _r: httpx.Response(429, json={"error": "invalid_grant"}))
    _refresh(monkeypatch)

    with pytest.raises(OAuthRefreshError) as ei:
        _call(mod)
    assert ei.value.kind == "network", "429 means slow down, not reconnect"


def test_a_2xx_with_an_unparseable_body_is_network(token_endpoint, monkeypatch):
    """**200** with HTML — a captive portal.

    Specified as a 500 in the plan's first draft, which the non-2xx branch
    consumes before this site is ever reached, so that site could have shipped
    broken with the suite green.
    """
    from services import email_oauth as mod

    token_endpoint(lambda _r: httpx.Response(200, text="<html>sign in to wifi</html>"))
    _refresh(monkeypatch)

    with pytest.raises(OAuthRefreshError) as ei:
        _call(mod)
    assert ei.value.kind == "network"


# --- consumer 1: mailbox health --------------------------------------------- #


@pytest.mark.parametrize("kind", ["credential", "network", "config", "egress_blocked"])
def test_the_health_classifier_reports_the_kind(kind):
    """The ticket's actual ask, at the layer the household member sees."""
    from signals import feed_monitor as fm

    assert fm._classify_construction_failure(OAuthRefreshError("boom", kind=kind)) == kind


def test_a_non_oauth_failure_still_classifies_as_before():
    """The new branch must not shadow the existing ones."""
    from signals import feed_monitor as fm

    assert fm._classify_construction_failure(ValueError("bad port")) == "config"
    assert fm._classify_construction_failure(RuntimeError("who knows")) == "credential"


# --- consumer 2: the SMTP send path ----------------------------------------- #
#
# These drive `smtp_send._resolve_smtp` for real. An earlier version of this file
# did not: it re-implemented the mapping locally and grepped the source with
# `inspect.getsource`, so the mutation `transient = False` — which reverts the
# entire send-path change — passed all 85 tests in this area. Post-implementation
# review caught it. A test that restates the implementation cannot fail with it.


@pytest.mark.parametrize(
    ("kind", "expected_code"),
    [
        ("network", "smtp_send_unavailable"),
        ("egress_blocked", "smtp_send_unavailable"),
        ("credential", "smtp_auth_failed"),
        ("config", "smtp_auth_failed"),
    ],
)
def test_the_send_error_code_follows_the_kind(monkeypatch, kind, expected_code):
    """A transient refusal stops being reported as an auth failure."""
    from actions.errors import PermanentActionError
    from actions.handlers import smtp_send

    from services import email_credentials

    def _raise(uid, *, instance):
        raise OAuthRefreshError("token refresh failed for microsoft", kind=kind)

    monkeypatch.setattr(email_credentials, "load_smtp_connection", _raise)

    with pytest.raises(PermanentActionError) as ei:
        smtp_send._resolve_smtp("src-1", "u1")
    assert ei.value.code == expected_code


@pytest.mark.parametrize("kind", ["credential", "network", "config", "egress_blocked"])
def test_the_send_failure_stays_permanent_for_every_kind(monkeypatch, kind):
    """Permanence is load-bearing — but since LUM-762 it is load-bearing for the CODE.

    This docstring used to argue that a non-permanent failure would return the row
    to `approved` with a `run_after` that `_CLAIM_BY_ID_SQL` did not check, leaving a
    zombie that a future background executor would redrive into a **duplicate send**.
    All three premises are now gone: `fail_execution` and its `executing → approved`
    edge are deleted, the claim filters `run_after`, and `claim_next` does not exist.
    Every failure dead-letters on the first attempt, so the zombie cannot be created.

    What `PermanentActionError` still decides is the error **code** the composer
    renders — "the mailbox has no Drafts folder" rather than a generic failure. That
    is why this parametrisation stays: not to prevent a retry that is now
    structurally impossible, but to keep the code precise for every refresh kind.

    This test exists because an earlier draft of this very chunk proposed exactly
    that change. It asserts the raised *type*, not the presence of a source line.
    """
    from actions.errors import PermanentActionError
    from actions.handlers import smtp_send

    from services import email_credentials

    def _raise(uid, *, instance):
        raise OAuthRefreshError("nope", kind=kind)

    monkeypatch.setattr(email_credentials, "load_smtp_connection", _raise)

    with pytest.raises(PermanentActionError):
        smtp_send._resolve_smtp("src-1", "u1")


# --- the two static config sites, by value ---------------------------------- #


def test_a_missing_client_id_is_config(monkeypatch):
    """The case the ticket never named — an operator problem, not the user's.

    The AST guard only proves `kind=` is *present*; `kind="network"` here would
    pass it while telling a household member to wait for something that will
    never happen on its own.
    """
    from services import email_oauth as mod

    monkeypatch.setattr(mod, "_client_id", lambda _p: None)

    with pytest.raises(OAuthRefreshError) as ei:
        _call(mod)
    assert ei.value.kind == "config"


def test_an_unknown_provider_is_config(monkeypatch):
    from services import email_oauth as mod

    _refresh(monkeypatch)
    payload = dict(_PAYLOAD, provider="notaprovider")

    with pytest.raises(OAuthRefreshError) as ei:
        mod.get_access_token("u1", connector="imap", instance="src-1", payload=payload)
    assert ei.value.kind == "config"


# --- the code actually reaches the database --------------------------------- #
#
# The plan promised store-level assertions in `test_email_poll_health.py`'s shape
# and shipped direct classifier calls instead. Post-implementation review flagged
# the gap: a classifier returning the right string proves nothing if the value
# never reaches `sources.last_poll_error_code`, which is what the banner reads.


class _HealthStore:
    """Captures the health UPDATE, mirroring `test_email_poll_health.py`."""

    def __init__(self):
        self.updates: list[tuple] = []

    def execute(self, query, params=None):
        q = " ".join((query or "").split()).lower()
        if "update sources set last_poll_error_code" in q:
            self.updates.append((q, params))
        return

    def fetch_one(self, query, params=None):
        return None

    def fetch_all(self, query, params=None):
        return []


@pytest.mark.parametrize("kind", ["credential", "network", "config", "egress_blocked"])
def test_the_kind_is_written_to_the_column_the_banner_reads(monkeypatch, kind):
    from signals import feed_monitor as fm

    import config as _config

    store = _HealthStore()
    monkeypatch.setattr(_config, "get_metadata_store", lambda: store)

    # `_record_poll_health` + `_classify_construction_failure` is the pair the
    # construction site uses (`feed_monitor.py:185`, `:354`). NOT
    # `_record_connect_outcome`, which routes through the *connect* classifier —
    # and `OAuthRefreshError` deliberately never reaches that one, so it would
    # return None and CLEAR health instead of stamping it. Writing this test the
    # obvious way got that wrong; the failure is what surfaced it.
    fm._record_poll_health(
        "src-1",
        ok=False,
        error_code=fm._classify_construction_failure(OAuthRefreshError("boom", kind=kind)),
    )

    assert len(store.updates) == 1, "exactly one health write per failed connect"
    _q, params = store.updates[0]
    assert params[0] == kind, f"`sources.last_poll_error_code` must carry {kind!r}"
