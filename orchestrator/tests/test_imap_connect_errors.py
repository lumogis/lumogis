# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""What ``EmailImapSource._connect`` produces, per failure type (LUM-633).

**This file exists because of a defect a review round caught twice.**

`feed_monitor` decides a mailbox's health by classifying what escapes a
connecting adapter call. That classification is only sound if `_connect`'s
own exception contract is exact:

* Three types must escape **unwrapped**, because other layers catch them *by
  type*. `_connect_writeback` maps two of them to `PermanentActionError` so the
  proposal queue **dead-letters** a permanent policy block instead of retrying
  it forever (LUM-625).
* Everything else must be wrapped in :class:`ImapConnectError` carrying a
  ``kind``, so `feed_monitor` can tell an auth failure from a network one
  without importing ``imapclient`` — ``imap_transport`` is by convention its
  sole importer.

**The ordering of the ``except`` clauses inside `_connect` is the whole game,
and it is not visually obvious.** `ImapHostBlockedError` and
`ImapHostUnresolvedError` are both **``ValueError`` subclasses**, so a broad
``except ValueError`` written above the specific checks silently swallows them
into `ImapConnectError` — at which point `_connect_writeback`'s `except`
becomes dead code and a permanent block is retried forever.

Every other test of this feature injects a hand-built exception into a fake
adapter and so cannot see that mistake at all. This one drives the real
`_connect`, which is the only layer where the mistake is observable.
"""

from __future__ import annotations

import pytest
from models.signals import SourceConfig


def _source() -> SourceConfig:
    return SourceConfig(
        id="11111111-1111-4111-8111-111111111111",
        name="mailbox",
        source_type="imap",
        url="imap://mail.example.invalid",
        category="general",
        active=True,
        poll_interval=900,
        extraction_method="imap_metadata",
        css_selector_override=None,
        last_polled_at=None,
        last_signal_at=None,
        poll_cursor=None,
        user_id="alice",
    )


@pytest.fixture
def connect_raising(monkeypatch):
    """Build an adapter whose connect raises ``exc`` from the client factory.

    The factory runs *inside* `_connect`'s `imap_egress_scope`, which is where
    a real transport failure would surface — so this exercises the actual
    except-clause chain rather than a stand-in.
    """
    from services import email_credentials

    monkeypatch.setattr(email_credentials, "load_connection", lambda uid, *, instance: object())

    from adapters.email_imap_source import EmailImapSource

    def make(exc: BaseException):
        def factory(_conn):
            raise exc

        return EmailImapSource(_source(), client_factory=factory)

    return make


# --------------------------------------------------------------------------- #
# Escapes unwrapped — other layers catch these BY TYPE
# --------------------------------------------------------------------------- #
def test_egress_blocked_escapes_unwrapped(connect_raising):
    from services.egress_guard import EgressBlockedError

    # Keyword-only by design — it carries `.host`, which `_connect_writeback`
    # reads when building the dead-letter message.
    exc = EgressBlockedError(host="mail.example.invalid", port=993)
    with pytest.raises(EgressBlockedError) as caught:
        connect_raising(exc)._connect()
    assert caught.value is exc


def test_host_blocked_escapes_unwrapped(connect_raising):
    """The one that breaks LUM-625 if the except order is wrong.

    ``ImapHostBlockedError`` subclasses ``ValueError``, so a broad
    ``except ValueError`` placed above it wraps it away and
    ``_connect_writeback`` stops dead-lettering.
    """
    from services.email_credentials import ImapHostBlockedError

    exc = ImapHostBlockedError("host not permitted")
    with pytest.raises(ImapHostBlockedError) as caught:
        connect_raising(exc)._connect()
    assert caught.value is exc


def test_host_unresolved_escapes_unwrapped(connect_raising):
    """Also a ``ValueError`` subclass, and the reason `config` != `network`.

    It must stay distinguishable so a transient DNS failure is classified
    ``network`` at *both* the construction and connect sites, rather than
    ``config`` ("will not fix itself") at one and ``network`` at the other.
    """
    from services.email_credentials import ImapHostUnresolvedError

    exc = ImapHostUnresolvedError("did not resolve")
    with pytest.raises(ImapHostUnresolvedError) as caught:
        connect_raising(exc)._connect()
    assert caught.value is exc


# --------------------------------------------------------------------------- #
# Wrapped, with a kind the caller can dispatch on
# --------------------------------------------------------------------------- #
def test_an_auth_failure_is_wrapped_with_kind_auth(connect_raising):
    from adapters.email_imap_source import ImapConnectError

    class LoginError(Exception):
        """Stands in for ``imapclient.exceptions.LoginError``."""

    exc = LoginError("authentication failed")
    with pytest.raises(ImapConnectError) as caught:
        connect_raising(exc)._connect()
    assert caught.value.kind == "auth"
    assert caught.value.__cause__ is exc


def test_a_socket_failure_is_wrapped_with_kind_network(connect_raising):
    from adapters.email_imap_source import ImapConnectError

    exc = OSError("connection refused")
    with pytest.raises(ImapConnectError) as caught:
        connect_raising(exc)._connect()
    assert caught.value.kind == "network"
    assert caught.value.__cause__ is exc


def test_a_bare_valueerror_is_wrapped_not_mistaken_for_a_policy_block(connect_raising):
    """A plain ``ValueError`` is not one of the exempt subclasses.

    Pins the discrimination in the other direction: the exempt list must be
    matched by *type*, not by "is it a ValueError".
    """
    from adapters.email_imap_source import ImapConnectError

    exc = ValueError("something else entirely")
    with pytest.raises(ImapConnectError) as caught:
        connect_raising(exc)._connect()
    assert caught.value.kind == "network"


def test_a_generic_imap_protocol_error_is_network_not_auth(connect_raising):
    """`imaplib.IMAP4.error` is named `error` and covers protocol failures
    generally — not authentication. Classifying it as auth would tell a user to
    re-enter a password that is fine, the exact harm LUM-633 removes. Unknown
    connect failures fall to `network`, which is the honest answer.
    """
    from adapters.email_imap_source import ImapConnectError

    class error(Exception):  # noqa: N801 - mirrors imaplib.IMAP4.error's real name
        pass

    with pytest.raises(ImapConnectError) as caught:
        connect_raising(error("BAD command"))._connect()
    assert caught.value.kind == "network"
