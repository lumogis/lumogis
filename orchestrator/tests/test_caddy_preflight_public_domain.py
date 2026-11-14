# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""LUM-916: the Caddy preflight warns when sign-in is off on a public CADDY_DOMAIN.

``docker/caddy/preflight.sh`` runs as the Caddy container's entrypoint. It
classifies every site address in ``CADDY_DOMAIN``, prints a WARNING block to
stderr when ``AUTH_ENABLED`` is not ``true`` and any address is public, and
then execs Caddy. It never refuses to start.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
PREFLIGHT = REPO / "docker" / "caddy" / "preflight.sh"
COMPOSE = REPO / "docker-compose.yml"

pytestmark = pytest.mark.skipif(shutil.which("sh") is None, reason="needs a POSIX sh")


def _public_hosts(domain: str) -> list[str]:
    out = subprocess.run(
        ["sh", "-c", '. "$0"; lumogis_public_hosts "$1"', str(PREFLIGHT), domain],
        env={**os.environ, "LUMOGIS_PREFLIGHT_LIB": "1"},
        capture_output=True,
        text=True,
        check=True,
        cwd=REPO,  # holds files, so an unguarded wildcard would glob
    )
    return out.stdout.split()


def _run(env: dict[str, str], *cmd: str) -> subprocess.CompletedProcess:
    base = {k: v for k, v in os.environ.items() if k not in ("AUTH_ENABLED", "CADDY_DOMAIN")}
    return subprocess.run(
        ["sh", str(PREFLIGHT), *cmd],
        env={**base, **env},
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize(
    "domain",
    [
        ":80",
        ":443",
        "localhost",
        "localhost:443",
        "lumogis.localhost",
        "lumogis.local",
        "nas.lan",
        "x.home.arpa",
        "lumogis.internal",
        "lumogis.test",
        "homelab.tail123.ts.net",
        "https://homelab.tail123.ts.net",
        "nas",
        "LUMOGIS.LOCAL.",
        "127.0.0.1",
        "10.0.0.5",
        "172.16.0.1",
        "172.31.255.255",
        "192.168.1.5:8443",
        "169.254.1.1",
        "100.100.1.1",
        "[::1]:443",
        "[fd00::1]",
        "fe80::1",
        "lumogis.local, nas.lan",
    ],
)
def test_private_addresses(domain):
    assert _public_hosts(domain) == []


@pytest.mark.parametrize(
    "domain,expected",
    [
        ("lumogis.example.com", ["lumogis.example.com"]),
        ("http://LUMOGIS.Example.COM:8080", ["lumogis.example.com"]),
        ("https://lumogis.example.com/", ["lumogis.example.com"]),
        ("*.example.com", ["*.example.com"]),
        ("8.8.8.8:443", ["8.8.8.8"]),
        ("172.32.0.1", ["172.32.0.1"]),
        ("100.128.0.1", ["100.128.0.1"]),
        ("[2606:4700::1111]", ["2606:4700::1111"]),
        ("lumogis.local, lumogis.example.org", ["lumogis.example.org"]),
        ("evil.local.example.com", ["evil.local.example.com"]),
    ],
)
def test_public_addresses(domain, expected):
    assert _public_hosts(domain) == expected


def test_warns_when_auth_off_on_public_domain():
    r = _run({"AUTH_ENABLED": "false", "CADDY_DOMAIN": "lumogis.example.com"}, "echo", "started")
    assert r.returncode == 0
    assert r.stdout.strip() == "started"  # still execs the real command
    assert "WARNING: sign-in is OFF" in r.stderr
    assert "lumogis.example.com" in r.stderr


def test_auth_unset_counts_as_off():
    r = _run({"CADDY_DOMAIN": "lumogis.example.com"}, "true")
    assert r.returncode == 0
    assert "WARNING" in r.stderr


@pytest.mark.parametrize("auth", ["true", "TRUE", " true "])
def test_silent_when_auth_on(auth):
    r = _run({"AUTH_ENABLED": auth, "CADDY_DOMAIN": "lumogis.example.com"}, "true")
    assert r.returncode == 0
    assert r.stderr == ""


@pytest.mark.parametrize("domain", [":80", "lumogis.local", "homelab.tail123.ts.net"])
def test_silent_on_private_domain_with_auth_off(domain):
    r = _run({"AUTH_ENABLED": "false", "CADDY_DOMAIN": domain}, "true")
    assert r.returncode == 0
    assert r.stderr == ""


def test_exit_status_of_command_is_kept():
    r = _run({"AUTH_ENABLED": "false", "CADDY_DOMAIN": "lumogis.example.com"}, "false")
    assert r.returncode == 1


def test_compose_runs_caddy_through_the_preflight():
    caddy = yaml.safe_load(COMPOSE.read_text())["services"]["caddy"]
    assert caddy["entrypoint"] == ["/bin/sh", "/etc/caddy/preflight.sh"]
    assert caddy["command"][0] == "caddy"
    assert "./docker/caddy/preflight.sh:/etc/caddy/preflight.sh:ro" in caddy["volumes"]
    assert caddy["environment"]["AUTH_ENABLED"] == "${AUTH_ENABLED:-false}"
    assert caddy["environment"]["CADDY_DOMAIN"] == "${CADDY_DOMAIN:-:80}"
