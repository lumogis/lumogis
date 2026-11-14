# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""IMAP connection transport — direct or via the LUM-625 CONNECT egress proxy.

Pass 3 of LUM-625. When ``LUMOGIS_IMAP_EGRESS_PROXY`` is set (Docker deployments),
Core opens IMAP(S) connections by first tunnelling through the LUM-618 Squid proxy
with an HTTP ``CONNECT``; the proxy peeks the TLS SNI, allowlists the declared mail
host, and splices end-to-end (no MITM — the mail-host certificate is still
verified, `server_hostname` = the real host).

**Honest posture (do not overstate):**

* Proxy routing enforces the mail-host allowlist for the IMAP *transport path* at
  the network layer, and **closes the DNS-rebinding window** — Core connects to
  the *proxy* and never resolves the mail host, so the proxy owns resolution.
* It is **not** a hard wall against a *fully-compromised* Core. Core is dual-homed
  by design (it keeps a direct internet leg for LLM APIs / ntfy / feeds), so a
  compromised process can open a socket that bypasses this code entirely — the
  same bypassability class as the in-process Layer-1 guard. The gain over Layer 1
  is network-level enforcement of the actual IMAP egress + rebinding closure, not
  an independent guarantee against arbitrary compromise.

**Fail-closed:** a proxy var that is *set but invalid*, or a proxy that refuses
the ``CONNECT``, fails the connection — never a silent direct fallback that would
drop containment on a dual-homed Core. Hosts the Layer-2 allow file cannot express
(IDN / IPv6 / nonstandard port) fall back to the **direct** path (Layer 1 still
contains them), matching the Pass-2 skip-and-log contract.

This module (not the adapter) is the sole importer of ``imapclient``; imports stay
lazy so importing it never requires the dependency.
"""

from __future__ import annotations

import logging
import socket
from dataclasses import dataclass
from dataclasses import field

from services.egress_guard import EgressBlockedError

from services import egress_guard
from services import email_credentials

_log = logging.getLogger(__name__)

# RFC 6154 SPECIAL-USE folder flags → a stable role slug. Roles other than INBOX
# are hidden from the label-seeding picker (Sent/Drafts/Trash/Junk/All are not
# user-meaningful categories). ``\Archive`` is surfaced (a real user folder).
_SPECIAL_USE_ROLES: dict[bytes, str] = {
    b"\\Sent": "sent",
    b"\\Drafts": "drafts",
    b"\\Trash": "trash",
    b"\\Junk": "junk",
    b"\\All": "all",
    b"\\Archive": "archive",
    b"\\Flagged": "flagged",
}
_HIDDEN_ROLES = frozenset({"sent", "drafts", "trash", "junk", "all", "flagged"})
# Gmail's "[Gmail]/All Mail" has no SPECIAL-USE flag on some servers — hide by name.
_HIDDEN_NAMES = frozenset({"all mail", "[gmail]/all mail", "[gmail]"})


@dataclass
class ProbeResult:
    """Result of a read-only mailbox probe (LUM-644 pre-save live test)."""

    ok: bool
    message_count: int | None = None
    folders: list[dict] = field(default_factory=list)
    capabilities: list[str] = field(default_factory=list)
    proxy_unverified: bool = False


# Bounded read for the proxy's CONNECT response head.
_CONNECT_RESP_MAX = 4096
# Default bound for the proxy CONNECT handshake when the caller passes no timeout
# (the metadata-poll path). A proxy that accepts the TCP connection but never
# answers the CONNECT must not wedge the poll thread forever (LUM-625 review).
_PROXY_CONNECT_TIMEOUT_SECONDS = 30.0


class ImapEgressProxyError(EgressBlockedError):
    """The egress proxy refused (or could not be used for) the IMAP ``CONNECT``.

    Subclasses :class:`EgressBlockedError` so the write-back path's existing
    ``except EgressBlockedError`` maps a proxy refusal / misconfiguration to a
    permanent (dead-lettered) failure, and a poll simply aborts the tick.
    """


def _proxy_eligible(conn) -> bool:
    """True iff ``conn``'s host is expressible in the Layer-2 SNI allow file.

    A host the allow-file generator skips (IDN / IPv6 / nonstandard port) cannot
    be allowlisted at the proxy, so proxying it would hard-fail every connect.
    Such hosts take the direct path instead (Layer 1 still contains them),
    keeping the Pass-2 skip-and-log promise. Delegates to the shared
    ``email_egress_acl.proxy_eligible`` so the routing decision has a single
    source of truth (LUM-638).
    """
    from services import email_egress_acl

    return email_egress_acl.proxy_eligible(conn.host, conn.port)


def connect(conn, *, timeout: float | None = None):
    """Return a logged-in IMAP client for ``conn`` (proxy-routed if configured)."""
    try:
        proxy = egress_guard.imap_egress_proxy()
    except egress_guard.ImapEgressProxyConfigError as exc:
        # Set-but-invalid → fail closed (never silently connect direct).
        raise ImapEgressProxyError(host=conn.host, port=conn.port, message=str(exc)) from exc

    use_proxy = proxy is not None and getattr(conn, "use_ssl", True) and _proxy_eligible(conn)
    if not use_proxy:
        if proxy is not None and getattr(conn, "use_ssl", True):
            _log.info(
                "imap.egress.proxy_ineligible host=%s port=%s — direct (Layer 1 only)",
                conn.host,
                conn.port,
            )
        # Direct path. Core resolves the host, so re-validate its IPs against the
        # SSRF policy immediately before the socket opens; for a plaintext mailbox
        # also re-assert loopback (closes the plaintext-credential rebinding window).
        email_credentials.revalidate_imap_host(
            conn.host, require_loopback=not getattr(conn, "use_ssl", True)
        )
        from imapclient import IMAPClient

        client = IMAPClient(conn.host, port=conn.port, ssl=conn.use_ssl, timeout=timeout)
    else:
        client = _connect_via_proxy(conn, proxy, timeout)
    if getattr(conn, "auth_type", "app_password") == "xoauth2":
        # XOAUTH2: imapclient builds the SASL string from the access token.
        client.oauth2_login(conn.username, conn.oauth_access_token)
    else:
        client.login(conn.username, conn.password)
    return client


def _read_connect_response(sock: socket.socket) -> str:
    """Read the proxy's HTTP response head (up to the blank line)."""
    data = b""
    while b"\r\n\r\n" not in data and len(data) < _CONNECT_RESP_MAX:
        chunk = sock.recv(256)
        if not chunk:
            break
        data += chunk
    return data.decode("latin1", errors="replace")


def _connect_ok(status_line: str) -> bool:
    """True iff the proxy status line is a 2xx=200 success.

    Parses the status *code* token (``HTTP/1.1 200 …`` → ``200``) rather than
    substring-matching ``200``, so a refusal whose reason phrase contains ``200``
    (``HTTP/1.1 502 upstream returned 200``) is correctly treated as a failure.
    """
    parts = status_line.split()
    return len(parts) >= 2 and parts[0].startswith("HTTP/") and parts[1] == "200"


def _connect_via_proxy(conn, proxy: tuple[str, int], timeout: float | None):
    """Build an IMAPClient whose socket is an HTTP CONNECT tunnel through ``proxy``.

    Core dials the proxy; the proxy dials the mail host. TLS is negotiated
    end-to-end over the tunnel with ``server_hostname = conn.host`` (so the mail
    host's certificate is still verified, and its SNI is what the proxy splices on).
    """
    from imapclient import IMAPClient
    from imapclient.tls import IMAP4_TLS
    from imapclient.tls import wrap_socket

    proxy_host, proxy_port = proxy

    class _ProxyTunnelIMAP4_TLS(IMAP4_TLS):
        def _create_socket(self, sock_timeout):
            eff = (
                sock_timeout
                if sock_timeout is not None
                else (timeout if timeout is not None else _PROXY_CONNECT_TIMEOUT_SECONDS)
            )
            # Defensive: the host reached the CONNECT request line — reject any
            # control chars (belt to the load-time bare-host validation).
            if any(c in "\r\n" for c in self.host):
                raise ImapEgressProxyError(
                    host=self.host, port=self.port, message="illegal char in IMAP host"
                )
            sock = socket.create_connection((proxy_host, proxy_port), timeout=eff)
            sock.settimeout(eff)  # also bound the CONNECT response read
            try:
                req = (
                    f"CONNECT {self.host}:{self.port} HTTP/1.1\r\n"
                    f"Host: {self.host}:{self.port}\r\n\r\n"
                )
                sock.sendall(req.encode("latin1"))
                resp = _read_connect_response(sock)
                status_line = resp.split("\r\n", 1)[0]
                if not _connect_ok(status_line):
                    raise ImapEgressProxyError(
                        host=self.host,
                        port=self.port,
                        message=(
                            f"egress proxy refused CONNECT {self.host}:{self.port} "
                            f"({status_line!r})"
                        ),
                    )
            except Exception:
                sock.close()
                raise
            # TLS over the tunnel — cert + SNI keyed on the real mail host.
            return wrap_socket(sock, self.ssl_context, self.host)

    class _ProxyRoutedIMAPClient(IMAPClient):
        def _create_IMAP4(self):
            connect_timeout = getattr(self._timeout, "connect", None)
            return _ProxyTunnelIMAP4_TLS(self.host, self.port, self.ssl_context, connect_timeout)

    _log.debug(
        "imap.egress.proxy_connect proxy=%s:%s target=%s:%s",
        proxy_host,
        proxy_port,
        conn.host,
        conn.port,
    )
    return _ProxyRoutedIMAPClient(conn.host, port=conn.port, ssl=conn.use_ssl, timeout=timeout)


# ---- read-only probe + backfill helpers (LUM-644) ---------------------------


def _proxy_configured_for(conn) -> bool:
    """True iff a Layer-2 egress proxy is configured and would route ``conn``.

    On such deployments the Squid allow file only contains stored-credential
    hosts and is regenerated host-side, so a pre-save test host is CONNECT-refused.
    The pre-save live test is therefore a Layer-1 feature; probe returns
    ``proxy_unverified`` instead of attempting a doomed connection (LUM-644 R2).
    """
    try:
        proxy = egress_guard.imap_egress_proxy()
    except egress_guard.ImapEgressProxyConfigError:
        # Set-but-invalid proxy — treat as configured (can't verify) rather than
        # falling through to a direct connect that drops containment.
        return True
    return proxy is not None and getattr(conn, "use_ssl", True) and _proxy_eligible(conn)


def _role_for(flags) -> tuple[str | None, bool]:
    """Map a folder's IMAP flags to ``(role, hidden)`` via RFC 6154 SPECIAL-USE."""
    for f in flags or ():
        fb = f if isinstance(f, bytes) else str(f).encode("latin1", "replace")
        role = _SPECIAL_USE_ROLES.get(fb)
        if role is not None:
            return role, role in _HIDDEN_ROLES
    return None, False


def _probe_client(client) -> ProbeResult:
    """Run the read-only probe against a connected client (no state change)."""
    folders: list[dict] = []
    for flags, _delim, name in client.list_folders():
        fname = name.decode("utf-8", "replace") if isinstance(name, bytes) else str(name)
        role, hidden = _role_for(flags)
        if fname.lower() in _HIDDEN_NAMES:
            hidden = True
        folders.append({"name": fname, "role": role, "hidden": hidden, "selected": not hidden})

    message_count: int | None = None
    try:
        status = client.folder_status("INBOX", ["MESSAGES"])
        raw = status.get(b"MESSAGES")
        if raw is None:
            raw = status.get("MESSAGES")
        message_count = int(raw) if raw is not None else None
    except Exception as exc:  # partial probe: auth worked, INBOX status did not
        _log.info("imap.probe.folder_status_failed msg=%s", str(exc)[:120])

    caps: list[str] = []
    try:
        for c in client.capabilities():
            caps.append(c.decode("ascii", "replace") if isinstance(c, bytes) else str(c))
    except Exception:
        pass

    return ProbeResult(ok=True, message_count=message_count, folders=folders, capabilities=caps)


def probe(conn, *, user_id: str | None = None, timeout: float | None = None) -> ProbeResult:
    """Read-only pre-save mailbox probe: folders (RFC 6154) + INBOX count + caps.

    Wraps the connection in the Layer-1 ``imap_egress_scope`` (allows only this
    host). On a proxy-configured deployment the test is not attempted — returns
    ``proxy_unverified`` (see :func:`_proxy_configured_for`). Never mutates.
    Connect/login/timeout failures propagate to the caller for mapping.
    """
    if _proxy_configured_for(conn):
        return ProbeResult(ok=False, proxy_unverified=True)
    with egress_guard.imap_egress_scope(conn, user_id=user_id):
        client = connect(conn, timeout=timeout)
        try:
            return _probe_client(client)
        finally:
            try:
                client.logout()
            except Exception:
                pass
