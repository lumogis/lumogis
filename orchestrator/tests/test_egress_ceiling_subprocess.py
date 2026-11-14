# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Locked-hook isolation tests for the LUM-571 egress ceiling.

The real ``tethered.activate(locked=True)`` installs a permanent process-wide
audit hook, so it can NEVER be exercised in-process without poisoning the whole
pytest interpreter. Each assertion therefore runs in a throwaway child
interpreter via ``support/egress_ceiling_child.py`` and we assert its JSON verdict.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

_CHILD = Path(__file__).parent / "support" / "egress_ceiling_child.py"


def _run(mode: str) -> dict:
    proc = subprocess.run(
        [sys.executable, str(_CHILD), mode],
        capture_output=True,
        text=True,
        timeout=30,
    )
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    assert lines, f"child produced no stdout (stderr: {proc.stderr[-500:]})"
    verdict = json.loads(lines[-1])
    if verdict.get("skip"):
        pytest.skip(verdict.get("reason", "tethered unavailable in child"))
    return verdict


@pytest.mark.parametrize(
    "mode",
    [
        "allow",
        "block_loopback",
        "block_prod",
        "locked_no_token",
        "locked_with_token",
        # LUM-637: the ceiling fails closed on a tethered policy-check error.
        "fc_normal",
        "fc_policy_error",
        "fo_policy_error",
        # LUM-903: tethered 1.x closes LUM-637 F2 (helpers pinned by the lock) and
        # F1 (bytes / bytearray hosts skipped every check in 0.5.1).
        "fc_tamper_helper",
        "bytes_host",
        "bytes_host_loopback",
    ],
)
def test_locked_ceiling_behaviour(mode):
    verdict = _run(mode)
    assert verdict.get("ok") is True, f"{mode}: {verdict.get('detail')}"
