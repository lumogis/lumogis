# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""GRAPH_MODE parity test — inprocess vs. service.

Boots Core twice over the same `tests/fixtures/ada_lovelace.md` corpus
(once with the in-process plugin, once with the out-of-process
`lumogis-graph` service), snapshots `/graph/health` after each run, and
asserts the two snapshots are identical on the parity-relevant fields.

This is the contract test for the extraction. Failure means switching
`GRAPH_MODE` changes projection output, which would silently corrupt
operator state on rollover.

Marked `@pytest.mark.integration` and `@pytest.mark.slow` — skipped by
default in CI. Run explicitly:

    make test-graph-parity

The harness:
  * uses `docker compose down -v` between phases to start each phase
    from a clean Postgres/FalkorDB state (parity over a virgin
    projection is the only useful comparison; otherwise we're diffing
    leftover state from a previous run).
  * uses `tests/integration/wait_for_idle.py` rather than `time.sleep`
    so the gate is deterministic across machines.
  * uses `tests/integration/diff_snapshots.py` so a failure leaves a
    machine-readable diff in CI logs.

Skipped (not failed) if `docker` is not on PATH; this lets the test
file be collected by `pytest -m integration` without forcing every dev
to install Docker.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.slow]

REPO_ROOT = Path(__file__).resolve().parents[2]
HELPERS = Path(__file__).resolve().parent

CORE_BASE_OVERLAY = "docker-compose.yml:docker-compose.falkordb.yml:docker-compose.parity.yml"
# docker-compose.parity-premium.yml only adds a host port mapping for the
# lumogis-graph service, so it can ONLY be layered when the premium overlay
# is present (otherwise the lumogis-graph service has no image/build).
SERVICE_OVERLAY = (
    "docker-compose.yml:docker-compose.falkordb.yml"
    ":docker-compose.premium.yml:docker-compose.parity.yml"
    ":docker-compose.parity-premium.yml"
)

CORE_URL = os.environ.get("LUMOGIS_API_URL", "http://127.0.0.1:8000")
KG_URL = os.environ.get("LUMOGIS_KG_URL", "http://127.0.0.1:8001")
INGEST_TIMEOUT_S = float(os.environ.get("LUMOGIS_PARITY_INGEST_TIMEOUT", "180"))
DRAIN_TIMEOUT_S = float(os.environ.get("LUMOGIS_PARITY_DRAIN_TIMEOUT", "120"))


def _have_docker() -> bool:
    return shutil.which("docker") is not None


def _run(
    cmd: list[str],
    *,
    env: dict[str, str] | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess:
    """Run a subprocess; surface stderr in pytest output on failure."""
    proc = subprocess.run(
        cmd,
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
    )
    if check and proc.returncode != 0:
        sys.stderr.write(f"\n+ {' '.join(cmd)}\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}\n")
        raise AssertionError(f"command failed (exit {proc.returncode}): {' '.join(cmd)}")
    return proc


def _compose_env(compose_file: str, **overrides: str) -> dict[str, str]:
    env = os.environ.copy()
    env["COMPOSE_FILE"] = compose_file
    env.update(overrides)
    return env


def _wait_for_http(url: str, timeout: float = 90.0) -> None:
    deadline = time.monotonic() + timeout
    import urllib.error
    import urllib.request

    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=3) as resp:
                if resp.status == 200:
                    return
        except (urllib.error.URLError, ConnectionError, TimeoutError):
            pass
        time.sleep(2.0)
    raise AssertionError(f"timeout waiting for {url} to respond 200 within {timeout}s")


def _ingest(url: str) -> None:
    """POST {"path":"/fixtures"} to Core's /ingest.

    /fixtures is the bind-mount path defined in docker-compose.parity.yml.
    It deliberately sits OUTSIDE /data because the base compose mounts the
    host data root at /data:ro, which prevents creating sub-mountpoints
    inside it.
    """
    import urllib.request

    req = urllib.request.Request(
        f"{url.rstrip('/')}/ingest",
        data=json.dumps({"path": "/fixtures"}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=INGEST_TIMEOUT_S) as resp:
        assert resp.status in (200, 202), f"ingest returned HTTP {resp.status}"


def _snapshot_core(url: str, dest: Path) -> None:
    import urllib.request

    with urllib.request.urlopen(f"{url.rstrip('/')}/graph/health", timeout=10) as resp:
        dest.write_bytes(resp.read())


def _wait_for_idle(mode: str, url: str) -> None:
    helper = HELPERS / "wait_for_idle.py"
    _run(
        [
            sys.executable,
            str(helper),
            "--mode",
            mode,
            "--url",
            url,
            "--timeout",
            str(int(DRAIN_TIMEOUT_S)),
        ]
    )


def _compose_down(env: dict[str, str]) -> None:
    _run(["docker", "compose", "down", "-v", "--remove-orphans"], env=env, check=False)


# Services we need healthy before the parity assertion can run. We deliberately
# omit `librechat` because it's a chat UI with no role in `/ingest` or
# `/graph/health`, yet `docker compose up --wait` would block on its (slow,
# heavy) healthcheck. VERIFY-PLAN: previously brought up the full project,
# which timed out on librechat in dev environments.
_PARITY_CORE_SERVICES = (
    "orchestrator",
    "falkordb",
    "postgres",
    "qdrant",
    "mongodb",
    "ollama",
    "stack-control",
)
_PARITY_PREMIUM_EXTRA = ("lumogis-graph",)


def _compose_up(env: dict[str, str], *, include_premium: bool = False) -> None:
    services = list(_PARITY_CORE_SERVICES)
    if include_premium:
        services.extend(_PARITY_PREMIUM_EXTRA)
    _run(
        ["docker", "compose", "up", "-d", "--wait", "--wait-timeout", "180", *services],
        env=env,
    )


@pytest.fixture(scope="module")
def parity_fixture_present():
    fixture = REPO_ROOT / "tests" / "fixtures" / "ada_lovelace.md"
    if not fixture.exists():
        pytest.fail(f"missing fixture {fixture} — required by parity test")
    return fixture


@pytest.mark.skipif(not _have_docker(), reason="docker CLI not available")
def test_graph_parity_inprocess_vs_service(tmp_path: Path, parity_fixture_present: Path):
    """The same fixture corpus MUST yield identical FalkorDB state in both modes."""

    inprocess_snapshot = tmp_path / "snapshot_inprocess.json"
    service_snapshot = tmp_path / "snapshot_service.json"

    inprocess_env = _compose_env(CORE_BASE_OVERLAY, GRAPH_MODE="inprocess")
    try:
        _compose_down(inprocess_env)
        _compose_up(inprocess_env, include_premium=False)
        _wait_for_http(f"{CORE_URL}/")
        _ingest(CORE_URL)
        _wait_for_idle("core", CORE_URL)
        _snapshot_core(CORE_URL, inprocess_snapshot)
    finally:
        _compose_down(inprocess_env)

    service_env = _compose_env(SERVICE_OVERLAY, GRAPH_MODE="service")
    try:
        _compose_down(service_env)
        _compose_up(service_env, include_premium=True)
        _wait_for_http(f"{CORE_URL}/")
        _wait_for_http(f"{KG_URL}/health")
        _ingest(CORE_URL)
        _wait_for_idle("kg", KG_URL)
        _wait_for_idle("core", CORE_URL)
        _snapshot_core(CORE_URL, service_snapshot)
    finally:
        _compose_down(service_env)

    helper = HELPERS / "diff_snapshots.py"
    proc = subprocess.run(
        [sys.executable, str(helper), str(inprocess_snapshot), str(service_snapshot)],
        capture_output=True,
        text=True,
    )
    sys.stderr.write(proc.stderr)
    assert proc.returncode == 0, (
        f"GRAPH_MODE parity broken — diff_snapshots reported:\n{proc.stdout}"
    )
