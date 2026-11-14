# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""LUM-572 — compose e2e for the Layer-1 scoped egress guard.

`egress_guard.build_allowlist` composes the allowlist from the process
ENVIRONMENT (`POSTGRES_HOST`, `QDRANT_URL`, `OLLAMA_URL`, …) **and** from DB state
(the instance privacy mode). Every other test supplies both itself —
`orchestrator/tests/test_egress_guard_integration.py` drives a `TestClient` under
`mock.patch` — so the guard's *logic* is well covered and its *inputs* are not
covered at all.

That leaves a defect class with no owner: a compose overlay that renames a service,
reshapes a URL, or fails to merge a variable silently yields an allowlist that is
too narrow (blocking legitimate local traffic) or too wide (permitting egress the
guard exists to stop). Neither is a logic bug. This lane asserts the allowlist a
**real container actually computes**.

**Why there is no route-level 503 assertion here.** That design was tried and
rejected on review, for two independently fatal reasons:

* `build_allowlist` **adds every configured model's own endpoint to the allowlist**
  (`egress_guard.py:447-462`), so a model pointed at a "blocked" stub allowlists
  that stub. The guard blocks *secondary* egress during a call — never the model's
  own endpoint.
* `is_local_model` is a substring test for ``"ollama"`` (`config.py:327-334`) and a
  fresh Postgres defaults to `LOCAL_ONLY`, so a stub not URL-matching "ollama" is
  refused by **privacy policy before the guard runs** — and the flag-off control is
  refused identically, proving nothing.

Route wiring is already covered by unit tests. What they cannot cover is the
environment, which is what this lane owns.

**Every assertion here has a negative control**, because each has a degenerate form
that passes for the wrong reason — the failure mode this lane exists to prevent:

* "allowed hosts connect" would pass trivially if the scope allowed everything, so
  the off-allowlist host must be **blocked** in the same scope;
* "off-allowlist host is blocked" would pass with the guard **removed** if the host
  were merely unreachable, so its reachability is asserted **outside** the scope;
* the block must raise tethered's egress-blocked error, not a bare
  `ConnectionRefusedError` — which is what a disabled guard produces.

Run via `make egress-guard-test`. Requires Docker.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.slow]

_ROOT = Path(__file__).resolve().parents[2]
_COMPOSE = "docker-compose.egress-guard-test.yml"
_PROJECT = "lum572-egress-guard"

# Reachable on the compose network, named by nothing build_allowlist reads.
_OFFLIST_HOST = "stub-offlist"
_OFFLIST_PORT = 8080


def _docker_available() -> bool:
    if not shutil.which("docker"):
        return False
    return (
        subprocess.run(["docker", "info"], capture_output=True, text=True, check=False).returncode
        == 0
    )


# LUM-718 lesson: a lane that skips silently reports green while validating
# nothing. The escalation machinery in orchestrator/tests/integration/conftest.py
# is path-scoped to that directory and does NOT cover this tree, so CI sets
# LUMOGIS_REQUIRE_DOCKER=1 and a missing Docker becomes a hard failure instead of
# an invisible pass.
if not _docker_available():
    if os.environ.get("LUMOGIS_REQUIRE_DOCKER") == "1":
        raise RuntimeError(
            "LUMOGIS_REQUIRE_DOCKER=1 but Docker is unavailable — this lane would "
            "have skipped silently and reported success."
        )
    pytestmark.append(pytest.mark.skipif(True, reason="Docker not available"))


def _compose(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["docker", "compose", "-p", _PROJECT, "-f", _COMPOSE, *args],
        cwd=str(_ROOT),
        capture_output=True,
        text=True,
        check=check,
    )


# The probe runs inside the real orchestrator image against the real merged
# environment. `import config` FIRST is load-bearing: importing services.egress_guard
# cold hits a circular import (egress_guard -> privacy_mode -> actions.audit ->
# config -> connector_credentials -> _credential_internals -> actions.audit), which
# the running app avoids only through its own startup import order.
_PROBE = """
import json, socket, sys
import config  # noqa: F401 - must precede egress_guard; see the comment above
import tethered
from services.egress_guard import build_allowlist

allow = sorted(build_allowlist(user_id=None, for_ceiling=False))
out = {"allowlist": allow, "in_scope": {}, "out_of_scope": {}}

def probe(host, port):
    try:
        socket.create_connection((host, port), 5).close()
        return "allowed"
    except Exception as exc:
        return type(exc).__name__

for host, port in (("postgres", 5432), ("qdrant", 6333), (%(offlist)r, %(offport)d)):
    out["out_of_scope"][host] = probe(host, port)

with tethered.scope(allow=list(allow), allow_localhost=False, label="lum572-probe"):
    for host, port in (("postgres", 5432), ("qdrant", 6333), (%(offlist)r, %(offport)d)):
        out["in_scope"][host] = probe(host, port)

print("LUM572_RESULT " + json.dumps(out))
""" % {"offlist": _OFFLIST_HOST, "offport": _OFFLIST_PORT}


@pytest.fixture(scope="module")
def guard_probe_result():
    """Bring the stack up once and run every probe in a single container exec."""
    try:
        _compose("up", "-d", "--wait")
        proc = _compose("exec", "-T", "guard-probe", "python", "-c", _PROBE)
        line = next(
            ln for ln in proc.stdout.splitlines() if ln.startswith("LUM572_RESULT ")
        )
        yield json.loads(line[len("LUM572_RESULT ") :])
    finally:
        _compose("down", "-v", check=False)


# ── The allowlist is the real one, built from the real merged environment ────


def test_allowlist_contains_the_real_backends(guard_probe_result):
    """The env-merge regression this ticket was filed for.

    Read out of the running container, not recomputed by the test — the whole
    point is that the test must not supply the environment it is checking.
    """
    allow = guard_probe_result["allowlist"]
    assert "postgres" in allow
    assert "qdrant" in allow


def test_allowlist_excludes_the_offlist_host(guard_probe_result):
    # Without this, `test_allowlist_contains_the_real_backends` would pass just as
    # well for an allowlist of "everything".
    assert _OFFLIST_HOST not in guard_probe_result["allowlist"]


# ── Behaviour inside a real tethered scope ──────────────────────────────────


def test_local_backend_traffic_passes_inside_the_scope(guard_probe_result):
    in_scope = guard_probe_result["in_scope"]
    assert in_scope["postgres"] == "allowed"
    assert in_scope["qdrant"] == "allowed"


def test_offlist_host_is_blocked_inside_the_scope(guard_probe_result):
    assert guard_probe_result["in_scope"][_OFFLIST_HOST] != "allowed"


def test_the_offlist_host_is_genuinely_reachable(guard_probe_result):
    """The negative control, and the reason this lane is not vacuous.

    A block against an *unreachable* host is exactly what a **disabled** guard
    produces too, so the same connect is made outside the scope. If this ever
    fails, `test_offlist_host_is_blocked_inside_the_scope` is proving nothing.
    """
    assert guard_probe_result["out_of_scope"][_OFFLIST_HOST] == "allowed"


def test_the_block_is_attributable_to_the_guard(guard_probe_result):
    """Not merely "a connection failed" — a refused connection is what a disabled
    guard yields. tethered raises its own error type, and that is what is asserted.
    """
    blocked = guard_probe_result["in_scope"][_OFFLIST_HOST]
    assert "EgressBlocked" in blocked, f"expected a tethered egress block, got {blocked!r}"
