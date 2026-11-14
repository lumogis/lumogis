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

#: RFC 6052 well-known NAT64 prefix, and RFC 8215's local-use sibling. Both are
#: fixed and therefore detectable from an address alone. *Network-specific*
#: NAT64 prefixes (/32 /40 /48 /56 /64 at a site-chosen base) are not — nothing
#: in the address distinguishes them from ordinary global unicast.
_NAT64_WKP = ipaddress.ip_network("64:ff9b::/96")
_NAT64_LOCAL_USE = ipaddress.ip_network("64:ff9b:1::/48")

#: RFC 2765 "IPv4-translated" form, ``::ffff:0:a.b.c.d``. Distinct from the
#: IPv4-*mapped* ``::ffff:a.b.c.d`` that :attr:`ipv4_mapped` understands — note
#: the extra ``0:`` group — and Python has no property for it.
_V4_TRANSLATED = ipaddress.ip_network("::ffff:0:0:0/96")

#: Deprecated RFC 4291 IPv4-compatible form (``::a.b.c.d``). ``::/96`` minus
#: the unspecified and loopback addresses, which are handled by ordinary policy.
_V4_COMPATIBLE = ipaddress.ip_network("::/96")


def _embedded_ipv4(
    ip: ipaddress.IPv6Address,
) -> list[ipaddress.IPv4Address]:
    """IPv4 addresses tunnelled inside an IPv6 one, for every *fixed-prefix* form.

    **Not "every" form, and the distinction is load-bearing.** An adversarial
    review found this docstring previously claimed completeness it did not have.
    What is detectable from an address alone is exactly the set with a
    well-known prefix: IPv4-mapped, IPv4-translated, 6to4, Teredo, NAT64
    (well-known and local-use), and the deprecated IPv4-compatible form.

    What is **not** detectable, and is therefore out of scope by construction:
    *network-specific* NAT64 prefixes (RFC 6052 /32…/64 at a site-chosen base)
    and ISATAP under a non-link-local prefix (``<site-prefix>::5efe:a.b.c.d``).
    Both are indistinguishable from ordinary global unicast without knowing the
    site's translator configuration, and both require that translator to exist
    on the path before they reach anything. An operator running either should
    scope egress at the network layer, not here.


    Exists because several IPv6 encodings reach ``169.254.169.254`` while
    reporting ``is_link_local == False``, so the v6 branch's link-local check
    misses all of them (LUM-65 P1 #8). Measured against Python 3.12, not
    assumed:

    ===========================  =============  ==========  ==================
    form                         is_link_local  is_private  pre-fix outcome
    ===========================  =============  ==========  ==================
    ``::ffff:169.254.169.254``   False          True        silently allowed
    ``::ffff:a9fe:a9fe``         False          True        silently allowed
    ``2002:a9fe:a9fe::``         False          True        silently allowed
    ``2001::…:a9fe:a9fe``        False          True        silently allowed
    ``64:ff9b::169.254.169.254`` False          False       ``AttributeError``
    ===========================  =============  ==========  ==================

    The first four were accepted whenever ``LUMOGIS_ALLOW_PRIVATE_OUTBOUND_URLS``
    was true, which is the default — the actual bypass.

    The NAT64 row crashed rather than passing, and not for a reason specific to
    NAT64: the old line read ``ip.is_private or ip.is_unique_local``, and
    ``IPv6Address`` has no ``is_unique_local``. Python's ``or`` short-circuits,
    so the missing attribute was only ever evaluated when ``is_private`` was
    False — meaning **every public IPv6 address** raised. A v6-only relay could
    not be configured at all, and no test caught it because the fixtures are
    IPv4 and private-v6 only. ULA (``fc00::/7``) is already covered by
    ``is_private``, so the correct expression is simply ``is_private``.

    Returns a list because a Teredo address embeds two independent IPv4
    addresses (server and client) and both are reachable targets.
    """
    found: list[ipaddress.IPv4Address] = []

    mapped = ip.ipv4_mapped
    if mapped is not None:
        found.append(mapped)

    sixtofour = ip.sixtofour
    if sixtofour is not None:
        found.append(sixtofour)

    teredo = ip.teredo
    if teredo is not None:
        found.extend(teredo)  # (server, client) — both are real endpoints

    # NAT64: the well-known /96 embeds the v4 in the low 32 bits. RFC 8215's
    # local-use prefix is a /48, so the v4 sits in bits 40..71 per RFC 6052's
    # /48 layout — with byte 64..71 reserved (u-octet) and skipped.
    if ip in _NAT64_WKP or ip in _V4_TRANSLATED:
        found.append(ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF))

    if ip in _NAT64_LOCAL_USE:
        # RFC 6052 §2.2 /48 layout: prefix in bits 0..47, the first 16 bits of
        # the IPv4 at bits 48..63, a reserved u-octet at 64..71 that the address
        # format requires to be zero, then the last 16 bits of the IPv4 at
        # 72..87. The v4 is NOT in the low 32 bits — writing `64:ff9b:1::a.b.c.d`
        # produces a *different* embedded address (0.0.0.0 for a bare prefix),
        # which is worth stating because that literal reads like a valid
        # metadata vector and is not one.
        #
        # The u-octet is deliberately not required to be zero here: refusing to
        # decode a malformed embedding would let one bad byte skip the check,
        # and nothing legitimate lives under a prefix reserved for NAT64.
        n = int(ip)
        found.append(ipaddress.IPv4Address((((n >> 64) & 0xFFFF) << 16) | ((n >> 40) & 0xFFFF)))

    if ip in _V4_COMPATIBLE and not ip.is_unspecified and not ip.is_loopback:
        found.append(ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF))

    # Deduplicate while preserving order, so a doubly-encoded address is not
    # reported twice.
    seen: set[ipaddress.IPv4Address] = set()
    return [a for a in found if not (a in seen or seen.add(a))]


def _truthy_env(name: str, default: str = "true") -> bool:
    return os.environ.get(name, default).strip().lower() in ("1", "true", "yes", "on")


def _private_host_allowlist() -> frozenset[str]:
    raw = os.environ.get("LUMOGIS_OUTBOUND_PRIVATE_HOST_ALLOWLIST", "") or ""
    parts = re.split(r"[\s,]+", raw.strip())
    return frozenset(p.lower() for p in parts if p)


def _resolved_ips(hostname: str) -> list[str]:
    """Return unique string forms of all A/AAAA addresses for hostname.

    **Resolved with `flags=0`, deliberately — never `AI_ADDRCONFIG`.**

    This used to pass `AI_ADDRCONFIG`, which made the guard resolve the hostname
    differently from the code that then connects to it, and a validator that
    sees a different address set than the connector is not a guard at all.
    glibc drops every AAAA record under that flag when the host has no
    *globally scoped* IPv6 address — loopback ``::1`` does not count — which is
    the default Docker container. Meanwhile `socket.create_connection` (used by
    `smtplib`, and so by `smtp_transport.connect_smtp`) resolves with
    ``getaddrinfo(host, port, 0, SOCK_STREAM)``: no flag, AAAA restored, and
    RFC 6724 sorting puts the v6 record first.

    So a hostname with ``A=<public decoy>`` and ``AAAA=fd00::1`` passed
    validation on its A record and connected to the private v6 address. No DNS
    rebinding and no TOCTOU — one static record set defeated the check, which is
    why the documented rebinding residual risk below did not cover it.

    Resolving without the flag can only widen what the policy inspects, so the
    guard now sees a superset of what the connector can reach. It stayed hidden
    because a developer machine with global IPv6 makes the flag inert.
    """
    out: list[str] = []
    seen: set[str] = set()
    for family, _, _, _, sockaddr in socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM):
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

        # Judge any tunnelled IPv4 by the IPv4 rules before applying the v6
        # ones. Without this, `::ffff:169.254.169.254` and its four siblings
        # reach the metadata endpoint that the bare v4 form is refused for —
        # the v6 branch's `is_link_local` is False for every one of them.
        # Refuse if ANY embedded address violates: an address that decodes to
        # two targets is only as safe as its worst one.
        for embedded in _embedded_ipv4(ip):
            viol = _ip_policy_violation(
                embedded,
                hostname=hostname,
                allow_private=allow_private,
                allowlist=allowlist,
            )
            if viol is not None:
                return f"{viol} (reached via IPv4-in-IPv6 form {ip.compressed})"

        is_loop = ip.is_loopback
        # `is_private` already covers ULA fc00::/7. The old `or
        # ip.is_unique_local` named an attribute IPv6Address does not have, and
        # only failed to raise because `or` short-circuits on private addresses
        # — see :func:`_embedded_ipv4`.
        is_priv = ip.is_private

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


def validate_outbound_host(
    host: str,
    *,
    resolve_host: Callable[[str], list[str]] | None = None,
) -> None:
    """Raise ``ValueError`` if ``host`` must not be an outbound connection target.

    The bare-host sibling of :func:`validate_outbound_connector_base_url`, for
    protocols that have no URL to parse — SMTP submission (LUM-65). Shares
    :func:`_ip_policy_violation` deliberately: two copies of an SSRF policy is
    two places for the next person to fix only one of.

    An operator's LAN or loopback MTA is a legitimate target, so private
    addresses follow the same ``LUMOGIS_ALLOW_PRIVATE_OUTBOUND_URLS`` /
    allowlist policy as every other connector. Link-local (``169.254.0.0/16``
    and IPv6 ``fe80::/10``) is always refused — that is the cloud metadata
    endpoint, which no household mail relay has any reason to be.
    """
    host = (host or "").strip()
    if not host:
        raise ValueError("host must be a non-empty string")

    allow_private = _truthy_env("LUMOGIS_ALLOW_PRIVATE_OUTBOUND_URLS", "true")
    allowlist = _private_host_allowlist()
    resolver = resolve_host or _resolved_ips

    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        viol = _ip_policy_violation(
            literal, hostname=host, allow_private=allow_private, allowlist=allowlist
        )
        if viol:
            raise ValueError(viol)
        return

    try:
        idna_host = host.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise ValueError("host is not a valid IDNA hostname") from exc

    try:
        ips = resolver(idna_host)
    except OSError:
        # Same posture as the URL validator: a host that does not resolve here
        # cannot be range-checked, and refusing it would break offline and
        # split-horizon DNS setups. Residual DNS-rebinding risk is documented.
        return

    for ip_s in ips:
        viol = _ip_policy_violation(
            ipaddress.ip_address(ip_s),
            hostname=host,
            allow_private=allow_private,
            allowlist=allowlist,
        )
        if viol:
            raise ValueError(viol)


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
    #
    # The parse and the verdict are separated on purpose. This used to read
    # `try: addr = ip_address(host); ...; if viol: raise ValueError(viol)` inside
    # a `try` whose `except ValueError: pass` existed to mean "not a literal" —
    # so the raise was caught by its own handler and the entire literal branch
    # was dead code. Every literal fell through to the DNS path, where
    # `except OSError: return` and `if not ips: return` both fail OPEN. On a
    # v4-only container glibc refuses an IPv6 literal with EAI_ADDRFAMILY, so
    # `http://[::1]/` was ACCEPTED even with
    # LUMOGIS_ALLOW_PRIVATE_OUTBOUND_URLS=false — the flag an operator sets
    # precisely to stop that. The sibling `validate_outbound_host` already used
    # this sentinel shape; only this function had the swallow.
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None  # not a literal IP; treat as hostname
    if literal is not None:
        viol = _ip_policy_violation(
            literal, hostname=host, allow_private=allow_private, allowlist=allowlist
        )
        if viol:
            raise ValueError(viol)
        return

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
