# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""SSRF policy for outbound connector hosts — the encodings and the resolver.

This file exists because the guard had no dedicated tests. Its behaviour was
covered only incidentally, through the paperless connector and the system-mail
admin route, and three defects survived in the gap — two of them found by an
adversarial review *after* a clean security pass on the same code.

The cases below are grouped by the defect they pin, because each one was a
different kind of mistake:

* **Encoding** — the policy inspected an IPv6 address without noticing it
  carried an IPv4 one. Judged by the v6 rules, `::ffff:169.254.169.254` looks
  ordinary; `is_link_local` is False for every tunnelled form.
* **Resolution** — the guard resolved the hostname differently from the code
  that connects to it, so it inspected addresses the connector would not use
  and missed the ones it would.
* **Control flow** — the literal-IP verdict was raised inside a `try` whose
  `except` existed to mean "not a literal", so the whole branch was dead.
"""

from __future__ import annotations

import ipaddress
import socket

import pytest
from services.outbound_http_url import _embedded_ipv4
from services.outbound_http_url import _resolved_ips
from services.outbound_http_url import validate_outbound_connector_base_url
from services.outbound_http_url import validate_outbound_host

_METADATA = "169.254.169.254"


def _embed_rfc6052_48(prefix48: str, v4: str) -> str:
    """Build a correct RFC 6052 /48 embedding — v4 at bits 48..63 and 72..87.

    Written out rather than hard-coded because the obvious literal is wrong:
    `64:ff9b:1::169.254.169.254` *reads* like a NAT64 metadata vector but embeds
    0.0.0.0 under the /48 layout. An adversarial reviewer proposed exactly that
    string as a bypass; deriving the address here is what showed it was not one.
    """
    p = int(ipaddress.ip_address(prefix48))
    a = int(ipaddress.IPv4Address(v4))
    return str(ipaddress.IPv6Address(p | (((a >> 16) & 0xFFFF) << 64) | ((a & 0xFFFF) << 40)))


# ---------------------------------------------------------------------------
# Encoding — IPv4 tunnelled inside IPv6
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "host",
    [
        _METADATA,  # the bare v4, always refused
        "::ffff:169.254.169.254",  # IPv4-mapped, dotted
        "::ffff:a9fe:a9fe",  # IPv4-mapped, hex — same address
        "::ffff:0:169.254.169.254",  # RFC 2765 IPv4-translated (note the 0:)
        "2002:a9fe:a9fe::",  # 6to4
        "64:ff9b::169.254.169.254",  # NAT64 well-known prefix
        _embed_rfc6052_48("64:ff9b:1::", _METADATA),  # RFC 8215 local-use, /48 layout
        "::169.254.169.254",  # deprecated IPv4-compatible
        "fe80::1",  # v6 link-local proper
    ],
)
@pytest.mark.parametrize("allow_private", ["true", "false"])
def test_metadata_address_is_refused_in_every_encoding(
    host: str, allow_private: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Link-local is refused regardless of the private-address policy.

    Parametrized over both policy states deliberately: several of these forms
    report `is_private == True`, so under the default `allow_private=true` they
    were waved through by the private-address branch rather than caught by the
    link-local one.
    """
    monkeypatch.setenv("LUMOGIS_ALLOW_PRIVATE_OUTBOUND_URLS", allow_private)
    with pytest.raises(ValueError):
        validate_outbound_host(host)


@pytest.mark.parametrize(
    "host", ["8.8.8.8", "::ffff:8.8.8.8", "2606:4700::1111", "smtp.example.com"]
)
def test_public_hosts_still_reachable(host: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """The guard must not over-block — including on public IPv6.

    `2606:4700::1111` is the regression case for a defect that made **every**
    public IPv6 address raise `AttributeError`: the policy read
    `is_private or is_unique_local`, an attribute `IPv6Address` does not have,
    and `or` short-circuits so it was only evaluated for non-private addresses.
    No v6-only relay could be configured at all.
    """
    monkeypatch.setenv("LUMOGIS_ALLOW_PRIVATE_OUTBOUND_URLS", "true")
    monkeypatch.setattr("services.outbound_http_url._resolved_ips", lambda h: ["93.184.216.34"])
    validate_outbound_host(host)


def test_embedded_extraction_reports_only_fixed_prefix_forms() -> None:
    """Pins the documented scope so the docstring cannot drift back to "every".

    Network-specific NAT64 prefixes and ISATAP under a site prefix are
    undetectable from an address alone; claiming otherwise is what an earlier
    version of this docstring did.
    """
    assert _embedded_ipv4(ipaddress.IPv6Address("::ffff:169.254.169.254")) == [
        ipaddress.IPv4Address(_METADATA)
    ]
    # A site-chosen NAT64 prefix is indistinguishable from global unicast.
    assert _embedded_ipv4(ipaddress.IPv6Address("2001:db8:1:a9fe:a9:fe00::")) == []


# ---------------------------------------------------------------------------
# Resolution — the validator must see what the connector sees
# ---------------------------------------------------------------------------


def test_resolver_does_not_filter_by_address_family(monkeypatch: pytest.MonkeyPatch) -> None:
    """`_resolved_ips` must not pass AI_ADDRCONFIG.

    `socket.create_connection` — which `smtplib`, and so
    `smtp_transport.connect_smtp`, ultimately uses — resolves with
    `getaddrinfo(host, port, 0, SOCK_STREAM)`: no flags. glibc's AI_ADDRCONFIG
    drops every AAAA record when the host has no *globally scoped* IPv6 address
    (loopback does not count), which is the default Docker container.

    So a host with `A=<public decoy>` and `AAAA=fd00::1` was validated on its A
    record and connected on its AAAA. One static record set, no DNS rebinding.
    A guard that resolves differently from the connector is not a guard.
    """
    seen: dict = {}

    def _fake_getaddrinfo(host, port, *args, **kwargs):
        seen["args"] = args
        seen["flags"] = kwargs.get("flags", args[3] if len(args) > 3 else 0)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]

    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo)
    _resolved_ips("example.test")

    assert not seen["flags"] & socket.AI_ADDRCONFIG, (
        "AI_ADDRCONFIG hides AAAA records from the guard that the connector will use"
    )


def test_every_resolved_record_is_checked_not_just_the_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A safe A record must not launder an unsafe AAAA record."""
    monkeypatch.setenv("LUMOGIS_ALLOW_PRIVATE_OUTBOUND_URLS", "false")
    monkeypatch.setattr(
        "services.outbound_http_url._resolved_ips",
        lambda h: ["93.184.216.34", "fe80::1"],
    )
    with pytest.raises(ValueError):
        validate_outbound_host("mixed.example.test")


# ---------------------------------------------------------------------------
# Control flow — the literal-IP branch must actually reject
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("url", ["http://[::1]:11434/", "http://[fd00::1]/", "http://127.0.0.1/"])
def test_private_literal_is_refused_when_the_operator_disabled_private_egress(
    url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The literal branch used to be dead code, so these were all accepted.

    The verdict was raised inside the `try` whose `except ValueError: pass`
    meant "not a literal", so it swallowed its own rejection and fell through to
    the DNS path — where both `except OSError: return` and `if not ips: return`
    fail open. On a v4-only container glibc refuses an IPv6 literal outright, so
    `http://[::1]/` was accepted under the very flag set to prevent it.
    """
    monkeypatch.setenv("LUMOGIS_ALLOW_PRIVATE_OUTBOUND_URLS", "false")

    def _must_not_resolve(_h):
        raise AssertionError("literal IPs must be judged without DNS")

    monkeypatch.setattr("services.outbound_http_url._resolved_ips", _must_not_resolve)
    with pytest.raises(ValueError):
        validate_outbound_connector_base_url(url)


def test_link_local_literal_is_refused_even_when_private_egress_is_allowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LUMOGIS_ALLOW_PRIVATE_OUTBOUND_URLS", "true")
    monkeypatch.setattr(
        "services.outbound_http_url._resolved_ips",
        lambda h: (_ for _ in ()).throw(AssertionError("should not resolve a literal")),
    )
    with pytest.raises(ValueError):
        validate_outbound_connector_base_url(f"http://{_METADATA}/")
