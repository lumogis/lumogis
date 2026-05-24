# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Shared outbound HTTP URL validation for operator-configured connectors.

Mitigates accidental SSRF to cloud metadata and loopback while keeping
Docker/LAN deployments usable via ``LUMOGIS_ALLOW_PRIVATE_OUTBOUND_URLS`` and
``LUMOGIS_OUTBOUND_PRIVATE_HOST_ALLOWLIST`` (LUM-281).

Residual risk: DNS rebinding / TOCTOU between validation time and the
actual httpx request — operators should pin paperless on trusted networks.
"""

from __future__ import annotations

import ipaddress
import os
import re
import socket
from collections.abc import Callable
from urllib.parse import urlparse

_LINK_LOCAL_V4 = ipaddress.ip_network("169.254.0.0/16")


def _truthy_env(name: str, default: str = "true") -> bool:
    return os.environ.get(name, default).strip().lower() in ("1", "true", "yes", "on")


def _private_host_allowlist() -> frozenset[str]:
    raw = os.environ.get("LUMOGIS_OUTBOUND_PRIVATE_HOST_ALLOWLIST", "") or ""
    parts = re.split(r"[\s,]+", raw.strip())
    return frozenset(p.lower() for p in parts if p)


def _resolved_ips(hostname: str) -> list[str]:
    """Return unique string forms of all A/AAAA addresses for hostname."""
    hints = 0
    if hasattr(socket, "AI_ADDRCONFIG"):
        hints |= socket.AI_ADDRCONFIG
    out: list[str] = []
    seen: set[str] = set()
    for family, _, _, _, sockaddr in socket.getaddrinfo(
        hostname, None, type=socket.SOCK_STREAM, flags=hints
    ):
        ip = sockaddr[0]
        if ip not in seen:
            seen.add(ip)
            out.append(ip)
    return out


def _ip_policy_violation(
    ip: ipaddress.IPv4Address | ipaddress.IPv6Address,
    *,
    hostname: str,
    allow_private: bool,
    allowlist: frozenset[str],
) -> str | None:
    """Return human-readable rejection reason, or None if allowed."""
    if isinstance(ip, ipaddress.IPv4Address):
        if ip in _LINK_LOCAL_V4:
            return "IPv4 link-local 169.254.0.0/16 is always blocked (metadata risk)"
        is_loop = ip.is_loopback
        is_priv = ip.is_private or ip.is_reserved
    else:
        if ip.is_link_local:
            return "IPv6 link-local addresses are always blocked"
        is_loop = ip.is_loopback
        is_priv = ip.is_private or ip.is_unique_local

    if is_loop or is_priv:
        if allow_private:
            return None
        if hostname.lower() in allowlist:
            return None
        return (
            "host resolves to a private/loopback address but "
            "LUMOGIS_ALLOW_PRIVATE_OUTBOUND_URLS is false and hostname is not in "
            "LUMOGIS_OUTBOUND_PRIVATE_HOST_ALLOWLIST"
        )
    return None


def validate_outbound_connector_base_url(
    url: str,
    *,
    resolve_host: Callable[[str], list[str]] | None = None,
) -> None:
    """Raise ``ValueError`` if ``url`` must not be used for outbound connector HTTP.

    ``resolve_host`` is injectable for tests (defaults to DNS via
    :func:`_resolved_ips`).
    """
    if not isinstance(url, str) or not url.strip():
        raise ValueError("URL must be a non-empty string")
    if url.strip() != url:
        raise ValueError("URL must not have leading or trailing whitespace")

    parsed = urlparse(url)
    if parsed.username or parsed.password:
        raise ValueError("URL must not embed credentials in the authority (use sealed credentials)")
    scheme = (parsed.scheme or "").lower()
    if scheme not in {"http", "https"}:
        raise ValueError("URL must use http or https scheme")
    host = (parsed.hostname or "").strip()
    if not host:
        raise ValueError("URL must include a non-empty host")

    allow_private = _truthy_env("LUMOGIS_ALLOW_PRIVATE_OUTBOUND_URLS", "true")
    allowlist = _private_host_allowlist()
    resolver = resolve_host or _resolved_ips

    # Bracketed IPv6 / IPv4 literals — parse directly without DNS.
    try:
        addr = ipaddress.ip_address(host)
        viol = _ip_policy_violation(
            addr, hostname=host, allow_private=allow_private, allowlist=allowlist
        )
        if viol:
            raise ValueError(viol)
        return
    except ValueError:
        pass  # not a literal IP; treat as hostname

    # IDNA / punycode hostnames for resolution
    try:
        idna_host = host.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise ValueError("URL hostname is not a valid IDNA hostname") from exc

    try:
        ips = resolver(idna_host)
    except OSError:
        # Offline / test hosts (e.g. ``nextcloud.example.com``) may not resolve.
        # Without A/AAAA records we cannot apply the IP-range policy; rely on
        # scheme + host shape checks only. Residual DNS-rebinding risk is noted
        # in operator docs (LUM-281).
        return
    if not ips:
        return

    for ip_s in ips:
        ip = ipaddress.ip_address(ip_s)
        viol = _ip_policy_violation(
            ip, hostname=host, allow_private=allow_private, allowlist=allowlist
        )
        if viol:
            raise ValueError(viol)
