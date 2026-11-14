# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""LUM-625 Pass 3 — IMAP transport: direct vs. CONNECT-proxy routing.

Hermetic / loopback: covers the routing decision, proxy-config parsing (incl.
fail-closed on a bad value and IPv6), the scope-target shift, the proxy-eligibility
fallback, the CONNECT handshake (200 happy path + refusal), and the scope↔transport
agreement. The end-to-end TLS-over-tunnel is additionally the Docker integration test.
"""

from __future__ import annotations

import socket
import sys
import threading
import types

import pytest

from services import egress_guard as eg
from services import imap_transport


class _Conn:
    def __init__(self, host="imap.example.com", use_ssl=True, port=993):
        self.host = host
        self.port = port
        self.use_ssl = use_ssl
        self.username = "u"
        self.password = "p"
        self.auth_type = "app_password"
        self.oauth_access_token = None


class _FakeLoginClient:
    def __init__(self):
        self.logged_in = False

    def login(self, u, p):
        self.logged_in = True


@pytest.fixture(autouse=True)
def _clear_proxy_env(monkeypatch):
    monkeypatch.delenv("LUMOGIS_IMAP_EGRESS_PROXY", raising=False)


def _fake_proxy(response: bytes) -> tuple[int, list]:
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    seen: list = []

    def _handle():
        try:
            conn, _ = srv.accept()
            seen.append(conn.recv(256))  # the CONNECT request
            conn.sendall(response)
            conn.close()
        except OSError:
            pass

    threading.Thread(target=_handle, daemon=True).start()
    return srv.getsockname()[1], seen


# --- proxy config parsing (fail-closed + IPv6) ----------------------------- #


def test_imap_egress_proxy_parsing(monkeypatch):
    monkeypatch.setenv("LUMOGIS_IMAP_EGRESS_PROXY", "egress-proxy:3128")
    assert eg.imap_egress_proxy() == ("egress-proxy", 3128)
    monkeypatch.setenv("LUMOGIS_IMAP_EGRESS_PROXY", "[::1]:3128")  # bracketed IPv6
    assert eg.imap_egress_proxy() == ("::1", 3128)
    monkeypatch.delenv("LUMOGIS_IMAP_EGRESS_PROXY", raising=False)
    assert eg.imap_egress_proxy() is None  # unset → None (Layer-1 only)


@pytest.mark.parametrize("bad", ["no-port", "host:notaport", ":3128", "ho st:3128", "a:b:c"])
def test_imap_egress_proxy_set_but_invalid_raises(monkeypatch, bad):
    monkeypatch.setenv("LUMOGIS_IMAP_EGRESS_PROXY", bad)
    with pytest.raises(eg.ImapEgressProxyConfigError):
        eg.imap_egress_proxy()


def test_target_host_is_proxy_when_proxying_tls(monkeypatch):
    monkeypatch.setenv("LUMOGIS_IMAP_EGRESS_PROXY", "egress-proxy:3128")
    assert eg.imap_egress_target_host(_Conn("imap.gmail.com")) == "egress-proxy"
    assert eg.imap_egress_target_host(_Conn("localhost", use_ssl=False)) == "localhost"
    monkeypatch.delenv("LUMOGIS_IMAP_EGRESS_PROXY", raising=False)
    assert eg.imap_egress_target_host(_Conn("imap.gmail.com")) == "imap.gmail.com"


def test_target_host_matches_real_routing_for_proxy_ineligible(monkeypatch):
    """LUM-638: the scope-target predictor must agree with the real dialer.

    A TLS mailbox on a nonstandard port with a proxy configured takes the DIRECT
    path in ``connect()`` (``_proxy_eligible`` is False), so the Layer-1 scope
    target must be the MAIL host — not the proxy — or the scope self-blocks the
    real connection.
    """
    monkeypatch.setenv("LUMOGIS_IMAP_EGRESS_PROXY", "egress-proxy:3128")
    eligible = _Conn("imap.gmail.com", port=993)
    ineligible = _Conn("imap.gmail.com", port=1993)  # nonstandard IMAPS port
    # Sanity: the real dialer's decision.
    assert imap_transport._proxy_eligible(eligible) is True
    assert imap_transport._proxy_eligible(ineligible) is False
    # The predictor must mirror it exactly.
    assert eg.imap_egress_target_host(eligible) == "egress-proxy"
    assert eg.imap_egress_target_host(ineligible) == "imap.gmail.com"


def test_proxy_error_is_egress_blocked():
    assert issubclass(imap_transport.ImapEgressProxyError, eg.EgressBlockedError)


# --- routing decision ------------------------------------------------------- #


def test_connect_direct_path_revalidates_then_builds_client(monkeypatch):
    order: list[str] = []
    monkeypatch.setattr(
        "services.email_credentials.revalidate_imap_host",
        lambda h, require_loopback=False: order.append("revalidate"),
    )

    class _FakeClient:
        def __init__(self, host, port=None, ssl=None, timeout=None):
            order.append("construct")

        def login(self, u, p):
            order.append("login")

    fake = types.ModuleType("imapclient")
    fake.IMAPClient = _FakeClient
    monkeypatch.setitem(sys.modules, "imapclient", fake)
    imap_transport.connect(_Conn("imap.gmail.com"))
    assert order == ["revalidate", "construct", "login"]


def test_connect_xoauth2_uses_oauth2_login(monkeypatch):
    # LUM-686: an xoauth2 conn authenticates via imapclient.oauth2_login with the
    # access token, NOT login().
    monkeypatch.setattr(
        "services.email_credentials.revalidate_imap_host",
        lambda h, require_loopback=False: None,
    )
    seen: dict = {}

    class _FakeClient:
        def __init__(self, host, port=None, ssl=None, timeout=None):
            pass

        def login(self, u, p):
            seen["login"] = (u, p)

        def oauth2_login(self, u, t):
            seen["oauth2_login"] = (u, t)

    fake = types.ModuleType("imapclient")
    fake.IMAPClient = _FakeClient
    monkeypatch.setitem(sys.modules, "imapclient", fake)
    conn = _Conn("imap.gmail.com")
    conn.auth_type = "xoauth2"
    conn.oauth_access_token = "ACCESS-TOK"
    conn.password = None
    imap_transport.connect(conn)
    assert seen == {"oauth2_login": ("u", "ACCESS-TOK")}


def test_connect_app_password_uses_login_not_oauth2(monkeypatch):
    # Regression: an app_password conn still uses login(), never oauth2_login().
    monkeypatch.setattr(
        "services.email_credentials.revalidate_imap_host",
        lambda h, require_loopback=False: None,
    )
    seen: dict = {}

    class _FakeClient:
        def __init__(self, host, port=None, ssl=None, timeout=None):
            pass

        def login(self, u, p):
            seen["login"] = (u, p)

        def oauth2_login(self, u, t):  # pragma: no cover - must NOT be called
            seen["oauth2_login"] = (u, t)

    fake = types.ModuleType("imapclient")
    fake.IMAPClient = _FakeClient
    monkeypatch.setitem(sys.modules, "imapclient", fake)
    imap_transport.connect(_Conn("imap.gmail.com"))
    assert seen == {"login": ("u", "p")}


def test_connect_proxy_path_skips_host_resolution(monkeypatch):
    monkeypatch.setenv("LUMOGIS_IMAP_EGRESS_PROXY", "egress-proxy:3128")
    monkeypatch.setattr(
        "services.email_credentials.revalidate_imap_host",
        lambda h, require_loopback=False: pytest.fail("must not resolve host when proxying"),
    )
    client = _FakeLoginClient()
    monkeypatch.setattr(imap_transport, "_connect_via_proxy", lambda conn, proxy, timeout: client)
    out = imap_transport.connect(_Conn("imap.gmail.com"))
    assert out is client and client.logged_in


def test_invalid_proxy_config_fails_closed(monkeypatch):
    # Set-but-invalid must NOT silently connect direct (dropping containment).
    monkeypatch.setenv("LUMOGIS_IMAP_EGRESS_PROXY", "egress-proxy")  # no port
    monkeypatch.setattr(
        imap_transport,
        "_connect_via_proxy",
        lambda *a, **k: pytest.fail("should not reach proxy"),
    )
    with pytest.raises(imap_transport.ImapEgressProxyError):
        imap_transport.connect(_Conn("imap.gmail.com"))


def test_proxy_ineligible_host_falls_back_to_direct(monkeypatch):
    # A non-993 host can't be allowlisted at the proxy → direct (Layer 1 only),
    # preserving the Pass-2 skip-and-log contract instead of hard-failing.
    monkeypatch.setenv("LUMOGIS_IMAP_EGRESS_PROXY", "egress-proxy:3128")
    seen: dict = {}
    monkeypatch.setattr(
        "services.email_credentials.revalidate_imap_host",
        lambda h, require_loopback=False: seen.setdefault("revalidate", h),
    )
    monkeypatch.setattr(
        imap_transport,
        "_connect_via_proxy",
        lambda *a, **k: pytest.fail("must not proxy ineligible host"),
    )

    class _FakeClient:
        def __init__(self, host, port=None, ssl=None, timeout=None):
            seen["client"] = (host, port)

        def login(self, u, p):
            pass

    fake = types.ModuleType("imapclient")
    fake.IMAPClient = _FakeClient
    monkeypatch.setitem(sys.modules, "imapclient", fake)
    imap_transport.connect(_Conn("imap.example.com", port=1993))
    assert seen["client"] == ("imap.example.com", 1993) and seen["revalidate"] == "imap.example.com"


def test_plaintext_direct_requires_loopback(monkeypatch):
    # use_ssl=False can't be spliced → direct, and revalidate must re-assert loopback.
    monkeypatch.setenv("LUMOGIS_IMAP_EGRESS_PROXY", "egress-proxy:3128")
    seen: dict = {}
    monkeypatch.setattr(
        "services.email_credentials.revalidate_imap_host",
        lambda h, require_loopback=False: seen.setdefault("require_loopback", require_loopback),
    )

    class _FakeClient:
        def __init__(self, *a, **k):
            pass

        def login(self, u, p):
            pass

    fake = types.ModuleType("imapclient")
    fake.IMAPClient = _FakeClient
    monkeypatch.setitem(sys.modules, "imapclient", fake)
    imap_transport.connect(_Conn("localhost", use_ssl=False))
    assert seen["require_loopback"] is True


# --- CONNECT handshake: happy path, refusal, status parse ------------------ #


def test_proxy_happy_path_sends_connect_and_wraps_tls(monkeypatch):
    class _Sentinel(Exception):
        pass

    captured: dict = {}

    def _fake_wrap(sock, ctx, host):
        captured["server_hostname"] = host
        sock.close()
        raise _Sentinel()  # stop before the imaplib greeting; we've proven the handoff

    monkeypatch.setattr("imapclient.tls.wrap_socket", _fake_wrap)
    port, seen = _fake_proxy(b"HTTP/1.1 200 Connection established\r\n\r\n")
    monkeypatch.setenv("LUMOGIS_IMAP_EGRESS_PROXY", f"127.0.0.1:{port}")
    with pytest.raises(_Sentinel):
        imap_transport.connect(_Conn("imap.example.com"))
    assert captured["server_hostname"] == "imap.example.com"  # TLS SNI = real mail host
    assert seen and b"CONNECT imap.example.com:993 HTTP/1.1" in seen[0]


def test_proxy_refused_connect_raises_typed_error(monkeypatch):
    port, _ = _fake_proxy(b"HTTP/1.1 403 Forbidden\r\n\r\n")
    monkeypatch.setenv("LUMOGIS_IMAP_EGRESS_PROXY", f"127.0.0.1:{port}")
    with pytest.raises(imap_transport.ImapEgressProxyError):
        imap_transport.connect(_Conn("imap.example.com"))


def test_connect_ok_rejects_200_in_reason_phrase():
    # A refusal whose reason phrase contains "200" must NOT be read as success.
    assert imap_transport._connect_ok("HTTP/1.1 200 Connection established") is True
    assert imap_transport._connect_ok("HTTP/1.1 502 upstream returned 200") is False
    assert imap_transport._connect_ok("HTTP/1.1 403 Forbidden") is False


def test_scope_allows_proxy_host_not_selfblocked(monkeypatch):
    # With the flag on and a proxy configured, imap_egress_scope must allow the
    # PROXY host — a proxied connect that reaches the proxy (and gets 403) proves
    # Layer 1 didn't self-block it (which it would if the scope kept conn.host).
    pytest.importorskip("tethered")
    monkeypatch.setenv("LUMOGIS_FF_IMAP_EGRESS_GUARD", "true")
    port, _ = _fake_proxy(b"HTTP/1.1 403 Forbidden\r\n\r\n")
    monkeypatch.setenv("LUMOGIS_IMAP_EGRESS_PROXY", f"127.0.0.1:{port}")
    conn = _Conn("imap.example.com")
    with pytest.raises(imap_transport.ImapEgressProxyError):
        with eg.imap_egress_scope(conn):
            imap_transport.connect(conn)
