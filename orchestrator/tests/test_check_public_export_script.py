# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Positive rejection harness for ``scripts/check-public-export.sh`` (LUM-242)."""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CHECK = REPO / "scripts/check-public-export.sh"


def _minimal_license() -> str:
    return """SPDX-License-Identifier: AGPL-3.0-only
Copyright (C) Example

This program is free software licensed under AGPL-3.0-only.
"""


def test_check_public_export_rejects_forbidden_strip_list(tmp_path):
    (tmp_path / "LICENSE").write_text(_minimal_license(), encoding="utf-8")
    bad = tmp_path / "orchestrator" / "plugins" / "graph"
    bad.mkdir(parents=True)

    proc = subprocess.run(
        ["bash", str(CHECK), str(tmp_path)],
        capture_output=True,
        text=True,
        cwd=str(REPO),
    )
    combined = proc.stdout + proc.stderr
    assert proc.returncode != 0
    assert "orchestrator/plugins/graph" in combined
