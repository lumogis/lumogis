# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Trusted-proxy resolution for the per-IP rate-limiter (D5 fix).

Plan ``cross_device_lumogis_web`` Pass 0.2 step 16. Without
:func:`csrf._proxied_client_ip` Caddy collapses every login attempt
onto its own container IP and the per-IP failed-login bucket becomes
deployment-wide instead of per-client.
"""

from __future__ import annotations

import socket
import threading
from types import SimpleNamespace

import csrf
import pytest
from csrf import _parse_trusted_hostnames
from csrf import _parse_trusted_proxies
from csrf import _proxied_client_ip
from csrf import _reset_trusted_proxy_caches_for_tests
from csrf import _trusted_proxy_networks


def _req(peer: str | None, **headers) -> SimpleNamespace:
    """Build the smallest object that quacks like ``fastapi.Request`` here."""
    client = SimpleNamespace(host=peer) if peer is not None else None
    return SimpleNamespace(client=client, headers=dict(headers))


def test_no_client_returns_unknown():
    assert _proxied_client_ip(_req(None)) == "unknown"


def test_untrusted_peer_ignores_xff(monkeypatch):
    monkeypatch.setenv("LUMOGIS_TRUSTED_PROXIES", "10.99.99.99")
    _reset_trusted_proxy_caches_for_tests()
    assert (
        _proxied_client_ip(_req("203.0.113.10", **{"X-Forwarded-For": "198.51.100.1"}))
        == "203.0.113.10"
    )


def test_trusted_peer_walks_xff_right_to_left(monkeypatch):
    monkeypatch.setenv("LUMOGIS_TRUSTED_PROXIES", "172.20.0.0/16")
    _reset_trusted_proxy_caches_for_tests()
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
    _reset_trusted_proxy_caches_for_tests()
    assert _proxied_client_ip(_req("127.0.0.1")) == "127.0.0.1"


def test_empty_allowlist_trusts_no_forwarded_headers(monkeypatch):
    """Empty env → no trusted proxies; XFF is ignored.

    docker-compose.yml sets LUMOGIS_TRUSTED_PROXIES explicitly for Caddy.
    Direct orchestrator callers must not be able to spoof the rate-limit key
    by adding their own X-Forwarded-For header.
    """
    monkeypatch.delenv("LUMOGIS_TRUSTED_PROXIES", raising=False)
    _reset_trusted_proxy_caches_for_tests()
    assert (
        _proxied_client_ip(_req("172.20.0.5", **{"X-Forwarded-For": "198.51.100.7"}))
        == "172.20.0.5"
    )


def test_malformed_xff_entry_returns_last_valid(monkeypatch):
    monkeypatch.setenv("LUMOGIS_TRUSTED_PROXIES", "127.0.0.0/8")
    _reset_trusted_proxy_caches_for_tests()
    # Right-to-left walk: 'not-an-ip' is malformed → last valid hop wins.
    out = _proxied_client_ip(_req("127.0.0.1", **{"X-Forwarded-For": "not-an-ip, 127.0.0.1"}))
    # The walker pops 127.0.0.1 (trusted) first, then sees 'not-an-ip'
    # and returns the last valid (which was 127.0.0.1).
    assert out == "127.0.0.1"


def test_malformed_proxy_entry_is_dropped(monkeypatch, caplog):
    monkeypatch.setenv("LUMOGIS_TRUSTED_PROXIES", "10.0.0.0/33,127.0.0.0/8")
    _reset_trusted_proxy_caches_for_tests()
    caplog.set_level("WARNING")
    nets = _parse_trusted_proxies("10.0.0.0/33,127.0.0.0/8")
    assert len(nets) == 1
    assert "ignoring malformed LUMOGIS_TRUSTED_PROXIES entry" in caplog.text


# --- hostname entries (LUM-887) -------------------------------------------


class _Dns:
    """Fake ``getaddrinfo`` that answers like the real one (list of 5-tuples)."""

    def __init__(self, *addrs: str):
        self.addrs = list(addrs)
        self.calls: list[str] = []
        self.fail: Exception | None = None

    def __call__(self, host, port, *args, **kwargs):
        self.calls.append(host)
        if self.fail is not None:
            raise self.fail
        return [
            (socket.AF_INET6 if ":" in a else socket.AF_INET, socket.SOCK_STREAM, 6, "", (a, 0))
            for a in self.addrs
        ]


@pytest.fixture
def dns(monkeypatch):
    fake = _Dns("172.18.0.5")
    clock = {"t": 0.0}
    monkeypatch.setattr(csrf.socket, "getaddrinfo", fake)
    monkeypatch.setattr(csrf, "_now", lambda: clock["t"])
    fake.clock = clock
    _reset_trusted_proxy_caches_for_tests()
    yield fake
    _reset_trusted_proxy_caches_for_tests()


def test_hostname_entry_trusts_resolved_address(monkeypatch, dns):
    monkeypatch.setenv("LUMOGIS_TRUSTED_PROXIES", "caddy")
    assert (
        _proxied_client_ip(_req("172.18.0.5", **{"X-Forwarded-For": "198.51.100.7"}))
        == "198.51.100.7"
    )


def test_hostname_entry_does_not_trust_other_peers(monkeypatch, dns):
    monkeypatch.setenv("LUMOGIS_TRUSTED_PROXIES", "caddy")
    assert (
        _proxied_client_ip(_req("172.18.0.9", **{"X-Forwarded-For": "198.51.100.7"}))
        == "172.18.0.9"
    )


def test_hostname_resolution_cached_within_ttl(monkeypatch, dns):
    monkeypatch.setenv("LUMOGIS_TRUSTED_PROXIES", "caddy")
    dns.clock["t"] = 0.0
    _trusted_proxy_networks()
    dns.clock["t"] = 29.0
    _trusted_proxy_networks()
    assert len(dns.calls) == 1
    dns.clock["t"] = 31.0
    _trusted_proxy_networks()
    assert len(dns.calls) == 2


def test_hostname_resolution_failure_trusts_nothing_and_logs_once(monkeypatch, dns, caplog):
    monkeypatch.setenv("LUMOGIS_TRUSTED_PROXIES", "caddy")
    caplog.set_level("INFO")
    dns.fail = socket.gaierror("no such host")
    hdr = {"X-Forwarded-For": "198.51.100.7"}
    dns.clock["t"] = 0.0
    assert _proxied_client_ip(_req("172.18.0.5", **hdr)) == "172.18.0.5"
    dns.clock["t"] = 31.0
    assert _proxied_client_ip(_req("172.18.0.5", **hdr)) == "172.18.0.5"
    assert len(dns.calls) == 2
    assert caplog.text.count("did not resolve") == 1
    dns.fail = None
    dns.clock["t"] = 62.0
    assert _proxied_client_ip(_req("172.18.0.5", **hdr)) == "198.51.100.7"
    assert caplog.text.count("resolves again") == 1


def test_negative_cache_expires_after_thirty_seconds(monkeypatch, dns):
    monkeypatch.setenv("LUMOGIS_TRUSTED_PROXIES", "caddy")
    dns.fail = socket.gaierror("no such host")
    dns.clock["t"] = 0.0
    assert _trusted_proxy_networks() == ()
    dns.fail = None
    dns.clock["t"] = 29.0
    assert _trusted_proxy_networks() == ()
    assert len(dns.calls) == 1
    dns.clock["t"] = 31.0
    assert len(_trusted_proxy_networks()) == 1
    assert len(dns.calls) == 2


def test_concurrent_lookup_does_not_wait_on_inflight_resolve(monkeypatch, dns):
    monkeypatch.setenv("LUMOGIS_TRUSTED_PROXIES", "caddy")
    started = threading.Event()
    release = threading.Event()
    inner = dns.__call__

    def slow(host, port, *a, **kw):
        started.set()
        assert release.wait(5)
        return inner(host, port, *a, **kw)

    monkeypatch.setattr(csrf.socket, "getaddrinfo", slow)
    results: list = []
    t = threading.Thread(target=lambda: results.append(_trusted_proxy_networks()))
    t.start()
    assert started.wait(5)
    t0 = threading.Event()
    box: list = []
    waiter = threading.Thread(target=lambda: (box.append(_trusted_proxy_networks()), t0.set()))
    waiter.start()
    assert t0.wait(0.5), "second caller blocked on the in-flight DNS lookup"
    assert box[0] == ()
    release.set()
    t.join(5)
    waiter.join(5)
    assert len(dns.calls) == 1
    assert len(_trusted_proxy_networks()) == 1


def test_failed_refresh_drops_stale_positive(monkeypatch, dns):
    monkeypatch.setenv("LUMOGIS_TRUSTED_PROXIES", "caddy")
    dns.clock["t"] = 0.0
    assert len(_trusted_proxy_networks()) == 1
    dns.fail = socket.gaierror("gone")
    dns.clock["t"] = 31.0
    assert _trusted_proxy_networks() == ()


def test_wide_private_static_entry_warns_once(monkeypatch, caplog):
    old = "127.0.0.0/8,::1/128,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,fc00::/7"
    monkeypatch.setenv("LUMOGIS_TRUSTED_PROXIES", old)
    _reset_trusted_proxy_caches_for_tests()
    caplog.set_level("WARNING")
    assert len(_trusted_proxy_networks()) == 6
    assert caplog.text.count("trusts a whole private range") == 4
    for net in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fc00::/7"):
        assert f"entry {net} trusts" in caplog.text
    assert "entry 127.0.0.0/8 trusts" not in caplog.text
    assert "entry ::1/128 trusts" not in caplog.text
    _trusted_proxy_networks()
    assert caplog.text.count("trusts a whole private range") == 4

    caplog.clear()
    monkeypatch.setenv("LUMOGIS_TRUSTED_PROXIES", "100.64.0.0/10,172.18.0.1/32,192.168.1.0/24")
    _trusted_proxy_networks()
    assert caplog.text.count("trusts a whole private range") == 1
    assert "entry 100.64.0.0/10 trusts" in caplog.text


def test_hostname_with_multiple_addresses_trusts_all(monkeypatch, dns):
    monkeypatch.setenv("LUMOGIS_TRUSTED_PROXIES", "caddy")
    dns.addrs = ["172.18.0.5", "fd00::5"]
    hdr = {"X-Forwarded-For": "198.51.100.7"}
    assert _proxied_client_ip(_req("172.18.0.5", **hdr)) == "198.51.100.7"
    assert _proxied_client_ip(_req("fd00::5", **hdr)) == "198.51.100.7"


def test_mixed_static_and_hostname_entries(monkeypatch, dns):
    monkeypatch.setenv("LUMOGIS_TRUSTED_PROXIES", "10.9.9.9,caddy")
    hdr = {"X-Forwarded-For": "198.51.100.7"}
    assert _proxied_client_ip(_req("10.9.9.9", **hdr)) == "198.51.100.7"
    assert _proxied_client_ip(_req("172.18.0.5", **hdr)) == "198.51.100.7"
    assert len(_parse_trusted_proxies("10.9.9.9,caddy")) == 1


def test_numeric_lookalike_is_malformed_not_hostname(monkeypatch, dns, caplog):
    caplog.set_level("WARNING")
    raw = "10.0.0.256,10.0.0.0/33,caddy"
    monkeypatch.setenv("LUMOGIS_TRUSTED_PROXIES", raw)
    assert _parse_trusted_hostnames(raw) == ("caddy",)
    _trusted_proxy_networks()
    assert "'10.0.0.256'" in caplog.text and "'10.0.0.0/33'" in caplog.text
    assert dns.calls == ["caddy"]


def test_hostname_does_not_change_right_to_left_walk(monkeypatch, dns):
    monkeypatch.setenv("LUMOGIS_TRUSTED_PROXIES", "caddy")
    out = _proxied_client_ip(_req("172.18.0.5", **{"X-Forwarded-For": "203.0.113.9, 198.51.100.7"}))
    assert out == "198.51.100.7"


def test_unset_env_still_trusts_nothing(monkeypatch, dns):
    monkeypatch.delenv("LUMOGIS_TRUSTED_PROXIES", raising=False)
    assert _trusted_proxy_networks() == ()
    assert dns.calls == []


def test_underscore_service_name_is_hostname(caplog):
    caplog.set_level("WARNING")
    _reset_trusted_proxy_caches_for_tests()
    assert _parse_trusted_hostnames("my_caddy,bad host,-x") == ("my_caddy",)
    _parse_trusted_proxies("my_caddy,bad host,-x")
    assert "'bad host'" in caplog.text and "'-x'" in caplog.text
