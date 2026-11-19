# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""services.home_dns.validate — pure validators (LUM-331)."""

from __future__ import annotations

import pytest
from services.home_dns import validate as v

HID = "kq7f3r2abcdefghijklmnopqrs"  # 26 chars, base32lower alphabet


def test_household_id() -> None:
    assert len(HID) == 26
    assert v.valid_household_id(HID)
    for bad in [HID[:25], HID + "a", HID.upper(), HID[:25] + "0", HID[:25] + "9", HID + "\n", ""]:
        assert not v.valid_household_id(bad), bad


def test_name_for_derives_and_rejects(monkeypatch) -> None:
    monkeypatch.delenv("LUMOGIS_HOME_DNS_ZONE", raising=False)
    assert v.name_for(HID) == f"{HID}.homes.lumogis.app"
    with pytest.raises(ValueError):
        v.name_for("evil.homes.lumogis.app}\nimport /etc/passwd")


def test_bearer() -> None:
    assert v.valid_bearer("A" * 43)
    assert v.valid_bearer("abc_DEF-123" * 4)
    for bad in ["A" * 42, "A" * 43 + " ", "A" * 43 + "}", "A" * 43 + "\n", "A" * 129, ""]:
        assert not v.valid_bearer(bad), repr(bad)


def test_server_url() -> None:
    assert v.valid_server_url("https://dns.homes.lumogis.app")
    assert v.valid_server_url("https://dns.example.test:8443")
    for bad in [
        "http://dns.homes.lumogis.app",
        "https://a b",
        "https://x/{y}",
        "https://x/path",
        "https://x?q=1",
        "https://user@x",
        "https://x\n",
        "https://",
    ]:
        assert not v.valid_server_url(bad), repr(bad)


def test_server_url_env_default_and_invalid(monkeypatch) -> None:
    monkeypatch.delenv("LUMOGIS_HOME_DNS_URL", raising=False)
    assert v.server_url() == "https://dns.homes.lumogis.app"
    monkeypatch.setenv("LUMOGIS_HOME_DNS_URL", "https://dns.example.test/")
    assert v.server_url() == "https://dns.example.test"
    monkeypatch.setenv("LUMOGIS_HOME_DNS_URL", "http://insecure.example.test")
    with pytest.raises(ValueError):
        v.server_url()


@pytest.mark.parametrize("ip", ["192.168.1.20", "10.0.0.5", "172.16.3.4", "172.31.255.254"])
def test_lan_ipv4_accepts_rfc1918(ip: str) -> None:
    assert v.valid_lan_ipv4(ip)


@pytest.mark.parametrize(
    "ip",
    [
        "172.32.0.1",
        "100.64.0.1",
        "169.254.1.1",
        "127.0.0.1",
        "8.8.8.8",
        "192.168.001.020",
        "::ffff:192.168.1.20",
        "fd00::1",
        "192.168.1.256",
        "",
        "192.168.1.20 ",
        "192.168.1.20\n",
        "192.168.1.20:80",
    ],
)
def test_lan_ipv4_rejects(ip: str) -> None:
    assert not v.valid_lan_ipv4(ip)


@pytest.mark.parametrize(
    "host",
    [
        f"{HID}.homes.lumogis.app",
        f"{HID}.homes.lumogis.app:8443",
        f"{HID.upper()}.HOMES.LUMOGIS.APP",
        "anything.homes.lumogis.app",  # fail closed for every name in the zone
    ],
)
def test_is_secure_zone_host_true(host: str, monkeypatch) -> None:
    monkeypatch.delenv("LUMOGIS_HOME_DNS_ZONE", raising=False)
    assert v.is_secure_zone_host(host)


@pytest.mark.parametrize(
    "host",
    [
        "homes.lumogis.app",
        "lumogis.app",
        "evilhomes.lumogis.app",
        "homes.lumogis.app.evil.test",
        "192.168.1.20",
        "localhost",
        "[fd00::1]:443",
        "",
        None,
    ],
)
def test_is_secure_zone_host_false(host, monkeypatch) -> None:
    monkeypatch.delenv("LUMOGIS_HOME_DNS_ZONE", raising=False)
    assert not v.is_secure_zone_host(host)
