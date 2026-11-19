# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""The egress ceiling allows Lumogis Home DNS and the in-network Caddy probe (LUM-331)."""

from __future__ import annotations

from services import egress_guard


def test_ceiling_includes_configured_home_dns_host(monkeypatch) -> None:
    monkeypatch.setenv("LUMOGIS_HOME_DNS_URL", "https://dns.example.test:8443")
    hosts = egress_guard._ceiling_extra_hosts()
    assert "dns.example.test" in hosts
    assert "caddy" in hosts


def test_ceiling_includes_default_home_dns_host_when_env_unset(monkeypatch) -> None:
    """Core falls back to the code default; the ceiling must follow the same value."""
    monkeypatch.delenv("LUMOGIS_HOME_DNS_URL", raising=False)
    hosts = egress_guard._ceiling_extra_hosts()
    assert "dns.homes.lumogis.app" in hosts
    assert "caddy" in hosts


def test_ceiling_survives_an_invalid_home_dns_url(monkeypatch) -> None:
    monkeypatch.setenv("LUMOGIS_HOME_DNS_URL", "http://not-https.example.test")
    hosts = egress_guard._ceiling_extra_hosts()
    assert "not-https.example.test" not in hosts
    assert "caddy" in hosts
