# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Pure validators for Lumogis Home DNS (LUM-331). No I/O, no project imports.

Every value Lumogis writes into Caddy config, sends to the Home DNS Worker or uses
to decide a security rule passes through here first. All checks use ``fullmatch``
(``$`` would accept a trailing newline). ``csrf.py``, ``routes/auth.py`` and
``services/egress_guard.py`` import this module, so it must stay dependency-free.
"""

from __future__ import annotations

import ipaddress
import os
import re

DEFAULT_ZONE = "homes.lumogis.app"
DEFAULT_SERVER_URL = "https://dns.homes.lumogis.app"

# Household id: the Worker's slug, base32lower(HMAC-SHA256(...))[:26].
_HOUSEHOLD_ID_RE = re.compile(r"[a-z2-7]{26}")
# Worker bearer token: base64url without padding, at least 256 bits.
_BEARER_RE = re.compile(r"[A-Za-z0-9_-]{43,128}")
# https://host[:port] — no path, query, credentials, whitespace or braces.
_SERVER_URL_RE = re.compile(r"https://([a-z0-9]([a-z0-9.-]{0,251}[a-z0-9])?)(:([1-9][0-9]{0,4}))?")
# Dotted quad without leading zeros (``192.168.001.020`` is ambiguous across parsers).
_OCTET = r"(?:25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9])"
_IPV4_RE = re.compile(rf"{_OCTET}\.{_OCTET}\.{_OCTET}\.{_OCTET}")
_RFC1918 = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
)


def zone() -> str:
    """The Home DNS zone (``LUMOGIS_HOME_DNS_ZONE``), lower-cased, default ``homes.lumogis.app``."""
    raw = os.environ.get("LUMOGIS_HOME_DNS_ZONE", "").strip().lower().rstrip(".")
    return raw or DEFAULT_ZONE


def host_without_port(host: str) -> str:
    """The host part of a ``Host`` header value: lower-cased, no port, no trailing dot.

    ``[v6]:port`` and ``[v6]`` give the bracket content; an IPv6 zone id
    (``%eth0``) is dropped; a bare IPv6 literal (more than one ``:``) is kept
    whole, because its last group is not a port. Returns ``""`` when nothing is
    left. Used by the secure-zone rule and by the setup claim's host guard.
    """
    h = host.strip().lower()
    if h.startswith("["):
        end = h.find("]")
        inner = h[1:end] if end != -1 else h[1:]
        return inner.split("%", 1)[0].strip().rstrip(".")
    if h.count(":") > 1:  # bare IPv6 literal
        return h.split("%", 1)[0].strip().rstrip(".")
    h = h.rstrip(".")
    name, sep, port = h.rpartition(":")
    if sep and port.isdigit():
        return name.rstrip(".")
    return h


# Old name, kept for existing callers.
_host_without_port = host_without_port


def is_secure_zone_host(host: str | None) -> bool:
    """True for any name *under* the Home DNS zone (``x.homes.lumogis.app[:port]``).

    Deliberately broad: the cross-site rule fails closed for every name in the zone,
    not only for well-formed household ids. The zone apex itself is not a household.
    """
    if not host:
        return False
    h = _host_without_port(host)
    return h.endswith("." + zone())


def valid_household_id(value: str) -> bool:
    return isinstance(value, str) and _HOUSEHOLD_ID_RE.fullmatch(value) is not None


def name_for(household_id: str) -> str:
    """``{household_id}.{zone}``; raises ``ValueError`` for an invalid id."""
    if not valid_household_id(household_id):
        raise ValueError("invalid household id")
    return f"{household_id}.{zone()}"


def valid_bearer(value: str) -> bool:
    return isinstance(value, str) and _BEARER_RE.fullmatch(value) is not None


def valid_server_url(value: str) -> bool:
    return isinstance(value, str) and _SERVER_URL_RE.fullmatch(value) is not None


def server_url() -> str:
    """The Worker base URL: ``LUMOGIS_HOME_DNS_URL`` or the default; raises if invalid."""
    raw = os.environ.get("LUMOGIS_HOME_DNS_URL", "").strip().rstrip("/") or DEFAULT_SERVER_URL
    if not valid_server_url(raw):
        raise ValueError("LUMOGIS_HOME_DNS_URL must be https://host[:port] with no path")
    return raw


def valid_lan_ipv4(value: str) -> bool:
    """RFC1918 IPv4 only (10/8, 172.16/12, 192.168/16) — the home-network addresses.

    Rejects CGNAT, link-local, loopback, public addresses, IPv6 (including
    IPv4-mapped), leading zeros, ports and surrounding whitespace.
    """
    if not isinstance(value, str) or _IPV4_RE.fullmatch(value) is None:
        return False
    addr = ipaddress.IPv4Address(value)
    return any(addr in net for net in _RFC1918)
