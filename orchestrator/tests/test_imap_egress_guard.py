# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""LUM-625 Layer 1 — in-process IMAP egress containment.

Proves that when ``LUMOGIS_FF_IMAP_EGRESS_GUARD`` is on, an IMAP connection is
scoped (via ``tethered``) so its only permitted egress host is the connection's
own ``conn.host`` — and that this covers every connection path (all four route
through ``EmailImapSource._connect``). Hermetic: all targets are loopback, so
no test performs real outbound egress.

``tethered`` matches on the *hostname* (LUM-625 spike), and the existing egress
suites gate on its presence, so these do too.
"""

from __future__ import annotations

import socket
import sys
import threading

import pytest
from actions.errors import PermanentActionError
from adapters.email_imap_source import EmailImapSource
from models.signals import SourceConfig
from services.egress_guard import EgressBlockedError

from services import egress_guard as eg

# An allowed host (loopback) and a *different* loopback address that is NOT the
# declared host — reaching it proves containment failed. Port 9 (discard) has no
# listener, so when the guard is off the connect fails fast with OSError rather
# than reaching anything real (LUM-570 precedent: keep block targets non-routable).
ALLOWED_HOST = "127.0.0.1"
UNDECLARED = ("127.0.0.2", 9)


@pytest.fixture(autouse=True)
def _no_proxy_env(monkeypatch):
    # These tests assume the direct connect path; a leaked LUMOGIS_IMAP_EGRESS_PROXY
    # (e.g. from the Docker overlay env in CI) would route through the proxy and
    # skip revalidate. Isolate it (LUM-625 Pass-3 review).
    monkeypatch.delenv("LUMOGIS_IMAP_EGRESS_PROXY", raising=False)


class _Conn:
    """Minimal stand-in for ``email_credentials.ImapConnection`` (host is all the scope reads)."""

    def __init__(self, host: str = ALLOWED_HOST) -> None:
        self.host = host
        self.port = 993
        self.username = "u"
        self.password = "p"
        self.use_ssl = True
        self.auth_type = "app_password"
        self.oauth_access_token = None


def _loopback_server() -> tuple[socket.socket, int]:
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)

    def _accept() -> None:
        try:
            conn, _ = srv.accept()
            conn.close()
        except OSError:
            pass

    threading.Thread(target=_accept, daemon=True).start()
    return srv, srv.getsockname()[1]


def _connect_to(target: tuple[str, int]) -> None:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.5)
    try:
        s.connect(target)
    finally:
        s.close()


# --------------------------------------------------------------------------- #
# imap_egress_scope — direct                                                   #
# --------------------------------------------------------------------------- #


def test_imap_scope_blocks_undeclared_host(monkeypatch):
    pytest.importorskip("tethered")
    monkeypatch.setenv("LUMOGIS_FF_IMAP_EGRESS_GUARD", "true")
    with pytest.raises(EgressBlockedError):
        with eg.imap_egress_scope(_Conn(ALLOWED_HOST)):
            _connect_to(UNDECLARED)


def test_imap_scope_allows_declared_host(monkeypatch):
    pytest.importorskip("tethered")
    monkeypatch.setenv("LUMOGIS_FF_IMAP_EGRESS_GUARD", "true")
    srv, port = _loopback_server()
    try:
        # conn.host is the declared host → reaching it must NOT be blocked.
        with eg.imap_egress_scope(_Conn(ALLOWED_HOST)):
            _connect_to((ALLOWED_HOST, port))  # no EgressBlockedError
    finally:
        srv.close()


def test_flag_off_no_enforcement(monkeypatch):
    """Guard off ⇒ scope is a no-op passthrough (no regression vs pre-LUM-625)."""
    pytest.importorskip("tethered")
    monkeypatch.delenv("LUMOGIS_FF_IMAP_EGRESS_GUARD", raising=False)
    with pytest.raises(OSError) as ei:  # connection refused, NOT an egress block
        with eg.imap_egress_scope(_Conn(ALLOWED_HOST)):
            _connect_to(UNDECLARED)
    assert not isinstance(ei.value, EgressBlockedError)


def test_flag_composition_independent_of_egress_guard(monkeypatch):
    """The IMAP flag enforces on its own, without LUMOGIS_FF_EGRESS_GUARD (the LLM wrap)."""
    pytest.importorskip("tethered")
    monkeypatch.setenv("LUMOGIS_FF_EGRESS_GUARD", "false")
    monkeypatch.setenv("LUMOGIS_FF_IMAP_EGRESS_GUARD", "true")
    assert eg.imap_egress_enabled() is True
    with pytest.raises(EgressBlockedError):
        with eg.imap_egress_scope(_Conn(ALLOWED_HOST)):
            _connect_to(UNDECLARED)


def test_guard_unavailable_fail_safe(monkeypatch):
    """tethered import failure ⇒ email keeps working (fail-safe-to-functional), not hard-blocked."""
    monkeypatch.setenv("LUMOGIS_FF_IMAP_EGRESS_GUARD", "true")
    # Force ``import tethered`` inside the scope to raise ImportError.
    monkeypatch.setitem(sys.modules, "tethered", None)
    with pytest.raises(OSError) as ei:  # no scope active → plain connection error
        with eg.imap_egress_scope(_Conn(ALLOWED_HOST)):
            _connect_to(UNDECLARED)
    assert not isinstance(ei.value, EgressBlockedError)


# --------------------------------------------------------------------------- #
# All four connection paths route through _connect → contained for free         #
# --------------------------------------------------------------------------- #


def _source() -> SourceConfig:
    return SourceConfig(
        id="src-1",
        name="mail",
        source_type="imap",
        url="imap://h/1/1",
        category="",
        active=True,
        poll_interval=900,
        extraction_method="imap_metadata",
        css_selector_override=None,
        last_polled_at=None,
        last_signal_at=None,
        poll_cursor=None,
        user_id="u1",
    )


@pytest.fixture()
def contained_source(monkeypatch):
    """An EmailImapSource whose client_factory tries to reach an UNDECLARED host,
    so every path that opens a connection trips the scope."""
    pytest.importorskip("tethered")
    monkeypatch.setenv("LUMOGIS_FF_IMAP_EGRESS_GUARD", "true")
    monkeypatch.setattr(
        "services.email_credentials.load_connection",
        lambda user_id, instance=None: _Conn(ALLOWED_HOST),
    )

    def _evil_factory(conn):
        _connect_to(UNDECLARED)  # blocked by the scope before any client is built
        raise AssertionError("factory reached the network — scope did not contain it")

    return EmailImapSource(_source(), client_factory=_evil_factory)


def test_poll_path_contained(contained_source):
    with pytest.raises(EgressBlockedError):
        contained_source.poll()


def test_fetch_bodies_path_contained(contained_source):
    with pytest.raises(EgressBlockedError):
        contained_source.fetch_bodies([1])


def test_apply_writeback_path_contained_permanent(contained_source):
    # Write-back is queue-backed → a block must surface as PermanentActionError
    # (dead-letter), never an infinitely-retried transient error.
    with pytest.raises(PermanentActionError) as ei:
        contained_source.apply_writeback(uidvalidity=100, uid=1, op="mark_read")
    assert ei.value.code == "egress_blocked"


def test_reverse_writeback_path_contained_permanent(contained_source):
    with pytest.raises(PermanentActionError) as ei:
        contained_source.reverse_writeback(
            op="flag", mailbox="INBOX", message_id="<m@x>", flag="\\Flagged"
        )
    assert ei.value.code == "egress_blocked"


# --------------------------------------------------------------------------- #
# Real (non-factory) _connect path + connect-time rebinding re-check            #
# --------------------------------------------------------------------------- #


def test_real_connect_revalidates_before_client_construction(monkeypatch):
    """On the real path, `revalidate_imap_host` (the rebinding re-check) must run
    BEFORE `IMAPClient` is constructed — a refactor that reorders/deletes it would
    silently reopen the DNS-rebinding gap this closure exists to narrow."""
    import types

    pytest.importorskip("tethered")
    monkeypatch.setenv("LUMOGIS_FF_IMAP_EGRESS_GUARD", "true")
    monkeypatch.setattr(
        "services.email_credentials.load_connection",
        lambda uid, instance=None: _Conn(ALLOWED_HOST),
    )
    order: list[str] = []
    monkeypatch.setattr(
        "services.email_credentials.revalidate_imap_host",
        lambda host, require_loopback=False: order.append("revalidate"),
    )

    class _FakeIMAPClient:
        def __init__(self, host, port=None, ssl=None, timeout=None):
            order.append("construct")

        def login(self, u, p):
            order.append("login")

    fake_mod = types.ModuleType("imapclient")
    fake_mod.IMAPClient = _FakeIMAPClient
    monkeypatch.setitem(sys.modules, "imapclient", fake_mod)

    src = EmailImapSource(_source())  # no client_factory → real path
    src._connect()
    assert order == ["revalidate", "construct", "login"]


def test_revalidate_imap_host_blocks_rebound_metadata_ip(monkeypatch):
    """A host that (re)resolves to the cloud-metadata IP is refused with the
    distinct ImapHostBlockedError (permanent), regardless of the flag."""
    from services import email_credentials as ec

    monkeypatch.setattr(ec, "_resolve_host_ips", lambda host: ["169.254.169.254"])
    with pytest.raises(ec.ImapHostBlockedError):
        ec.revalidate_imap_host("mail.attacker.example")


def test_writeback_blocked_host_maps_to_permanent(monkeypatch):
    """A connect-time SSRF/policy block on the write-back path dead-letters
    (PermanentActionError), it is not retried as transient."""
    from services import email_credentials as ec

    monkeypatch.delenv("LUMOGIS_FF_IMAP_EGRESS_GUARD", raising=False)  # isolate the SSRF path
    monkeypatch.setattr(
        "services.email_credentials.load_connection",
        lambda uid, instance=None: _Conn("169.254.169.254"),
    )
    monkeypatch.setattr(ec, "_resolve_host_ips", lambda host: ["169.254.169.254"])
    src = EmailImapSource(_source())
    with pytest.raises(PermanentActionError) as ei:
        src.apply_writeback(uidvalidity=100, uid=1, op="mark_read")
    assert ei.value.code == "egress_blocked"
