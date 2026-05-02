# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Phase 3 grep gate, executed under pytest.

The shell script ``scripts/check_no_default_user.sh`` is the canonical
implementation; this test calls it so the gate runs as part of the
normal test suite. Failing here means a hot-path module under
``orchestrator/`` is silently re-introducing a ``user_id="default"``
fallback that Phase 3 explicitly forbade.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_GATE = _REPO_ROOT / "scripts" / "check_no_default_user.sh"


@pytest.mark.skipif(not _GATE.exists(), reason="grep gate script missing")
def test_no_default_user_id_in_hot_paths() -> None:
    """Run the Phase 3 grep gate. Non-zero exit → forbidden pattern present."""
    if not os.access(_GATE, os.X_OK):
        os.chmod(_GATE, 0o755)
    if shutil.which("grep") is None:
        pytest.skip("grep not available in PATH")
    proc = subprocess.run(
        [str(_GATE)],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, (
        f"Phase 3 grep gate failed:\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
