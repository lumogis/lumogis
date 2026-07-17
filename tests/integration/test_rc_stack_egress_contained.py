# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""LUM-618 — the P0 guard: the REAL RC stack render must be contained.

Every other containment test runs a bespoke fixture. This one renders the actual
RC-stack compose chain (base + test + public-rc-stack + egress overlay) and
asserts, via compose-policy Pass C, that the community mock is on the isolated
network ONLY — catching the failure mode where the RC stack drops the global
opt-in flag + marks the mock contained but never composes the egress overlay
(mock would run uncontained while the gate reports "contained").
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

_ROOT = Path(__file__).resolve().parents[2]
_CHAIN = [
    "docker-compose.yml",
    "docker-compose.test.yml",
    "docker-compose.public-rc-stack.yml",
    "docker-compose.egress.yml",
]
_MOCK = "lumogis-mock-capability"


def _docker_available() -> bool:
    if not shutil.which("docker"):
        return False
    return subprocess.run(
        ["docker", "info"], capture_output=True, text=True, check=False
    ).returncode == 0


skip_no_docker = pytest.mark.skipif(not _docker_available(), reason="Docker not available")


def _env() -> dict:
    env = dict(os.environ)
    env["COMPOSE_PROFILES"] = "community-egress"
    env.setdefault("MOCK_CAPABILITY_SHARED_SECRET", "lumogis-ci-mock-capability-placeholder")
    return env


@skip_no_docker
def test_pass_c_passes_on_rc_render() -> None:
    """check_compose_policy Pass C must exit 0 for the RC render (mock contained)."""
    args = ["python3", "scripts/check_compose_policy.py", "--project-directory", str(_ROOT)]
    for f in _CHAIN:
        args += ["-f", f]
    args += ["--community-service", _MOCK]
    proc = subprocess.run(
        args, cwd=str(_ROOT), capture_output=True, text=True, env=_env(), check=False
    )
    assert proc.returncode == 0, f"Pass C failed on RC render:\n{proc.stdout}\n{proc.stderr}"


@skip_no_docker
def test_rc_render_places_mock_on_isolated_network_only() -> None:
    """Belt-and-braces: assert the mock's rendered networks are isolated-only."""
    args = ["docker", "compose", "--project-directory", str(_ROOT)]
    for f in _CHAIN:
        args += ["-f", f]
    args += ["config", "--format", "json"]
    proc = subprocess.run(
        args, cwd=str(_ROOT), capture_output=True, text=True, env=_env(), check=True
    )
    data = json.loads(proc.stdout)
    networks = data.get("networks", {})
    mock = data["services"][_MOCK]
    mock_nets = mock.get("networks")
    net_names = list(mock_nets.keys()) if isinstance(mock_nets, dict) else list(mock_nets or [])
    assert net_names, "mock is on no explicit network (would fall to the default bridge)"
    for n in net_names:
        assert networks.get(n, {}).get("internal") is True, (
            f"mock is on non-isolated network {n!r} — not contained"
        )
