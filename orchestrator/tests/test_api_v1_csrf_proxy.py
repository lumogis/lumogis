# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Trusted-proxy resolution for the per-IP rate-limiter (D5 fix).

Plan ``cross_device_lumogis_web`` Pass 0.2 step 16. Without
:func:`csrf._proxied_client_ip` Caddy collapses every login attempt
onto its own container IP and the per-IP failed-login bucket becomes
deployment-wide instead of per-client.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from csrf import _parse_trusted_proxies, _proxied_client_ip


def _req(peer: str | None, **headers) -> SimpleNamespace:
    """Build the smallest object that quacks like ``fastapi.Request`` here."""
    client = SimpleNamespace(host=peer) if peer is not None else None
    return SimpleNamespace(client=client, headers=dict(headers))


def test_no_client_returns_unknown():
    assert _proxied_client_ip(_req(None)) == "unknown"


def test_untrusted_peer_ignores_xff(monkeypatch):
    monkeypatch.setenv("LUMOGIS_TRUSTED_PROXIES", "10.99.99.99")
    _parse_trusted_proxies.cache_clear()
    assert (
        _proxied_client_ip(
            _req("203.0.113.10", **{"X-Forwarded-For": "198.51.100.1"})
        )
        == "203.0.113.10"
    )


def test_trusted_peer_walks_xff_right_to_left(monkeypatch):
    monkeypatch.setenv("LUMOGIS_TRUSTED_PROXIES", "172.20.0.0/16")
    _parse_trusted_proxies.cache_clear()
    # Caddy at 172.20.0.5 forwards a chain ending in itself; the first
    # untrusted hop walking right-to-left is 198.51.100.1.
    assert (
        _proxied_client_ip(
            _req(
                "172.20.0.5",
                **{"X-Forwarded-For": "198.51.100.1, 172.20.0.5"},
            )
        )
        == "198.51.100.1"
    )


def test_trusted_peer_no_xff_falls_back_to_peer(monkeypatch):
    monkeypatch.setenv("LUMOGIS_TRUSTED_PROXIES", "127.0.0.0/8")
    _parse_trusted_proxies.cache_clear()
    assert _proxied_client_ip(_req("127.0.0.1")) == "127.0.0.1"


def test_empty_allowlist_trusts_no_forwarded_headers(monkeypatch):
    """Empty env → no trusted proxies; XFF is ignored.

    docker-compose.yml sets LUMOGIS_TRUSTED_PROXIES explicitly for Caddy.
    Direct orchestrator callers must not be able to spoof the rate-limit key
    by adding their own X-Forwarded-For header.
    """
    monkeypatch.delenv("LUMOGIS_TRUSTED_PROXIES", raising=False)
    _parse_trusted_proxies.cache_clear()
    assert (
        _proxied_client_ip(
            _req("172.20.0.5", **{"X-Forwarded-For": "198.51.100.7"})
        )
        == "172.20.0.5"
    )


def test_malformed_xff_entry_returns_last_valid(monkeypatch):
    monkeypatch.setenv("LUMOGIS_TRUSTED_PROXIES", "127.0.0.0/8")
    _parse_trusted_proxies.cache_clear()
    # Right-to-left walk: 'not-an-ip' is malformed → last valid hop wins.
    out = _proxied_client_ip(
        _req("127.0.0.1", **{"X-Forwarded-For": "not-an-ip, 127.0.0.1"})
    )
    # The walker pops 127.0.0.1 (trusted) first, then sees 'not-an-ip'
    # and returns the last valid (which was 127.0.0.1).
    assert out == "127.0.0.1"


def test_malformed_proxy_entry_is_dropped(monkeypatch, caplog):
    monkeypatch.setenv("LUMOGIS_TRUSTED_PROXIES", "not-a-cidr,127.0.0.0/8")
    _parse_trusted_proxies.cache_clear()
    caplog.set_level("WARNING")
    nets = _parse_trusted_proxies("not-a-cidr,127.0.0.0/8")
    assert len(nets) == 1
