# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Harness for ``scripts/check-exec-bits.sh`` (LUM-784).

Builds a throwaway git repo so the check's contract is exercised on the INDEX
mode (``core.fileMode=false`` makes the filesystem mode irrelevant).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
CHECK = REPO / "scripts" / "check-exec-bits.sh"


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


def _make_repo(tmp_path: Path, files: dict[str, str], allowlist: str = "") -> Path:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "core.fileMode", "false")
    for rel, mode in files.items():
        f = tmp_path / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("#!/usr/bin/env bash\n")
        f.chmod(0o755)  # on disk it is executable; the index is what counts
        _git(tmp_path, "add", rel)
        if mode == "755":
            _git(tmp_path, "update-index", "--chmod=+x", rel)
    (tmp_path / "scripts").mkdir(exist_ok=True)
    (tmp_path / "scripts" / "exec-bit-allowlist.txt").write_text(allowlist)
    return tmp_path


def _run(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(CHECK), str(root)], text=True, capture_output=True, check=False
    )


@pytest.mark.skipif(
    subprocess.run(["git", "--version"], capture_output=True).returncode != 0,
    reason="git not available",
)
class TestCheckExecBits:
    def test_green_when_all_scripts_are_755(self, tmp_path: Path) -> None:
        root = _make_repo(tmp_path, {"docker/x/entrypoint.sh": "755", "scripts/a.sh": "755"})
        proc = _run(root)
        assert proc.returncode == 0, proc.stderr
        assert "OK" in proc.stdout

    def test_red_lists_every_non_executable_script(self, tmp_path: Path) -> None:
        root = _make_repo(
            tmp_path,
            {"docker/x/entrypoint.sh": "644", "scripts/a.sh": "755", "scripts/b.sh": "644"},
        )
        proc = _run(root)
        assert proc.returncode == 1
        assert "docker/x/entrypoint.sh" in proc.stderr
        assert "scripts/b.sh" in proc.stderr
        assert "scripts/a.sh" not in proc.stderr

    def test_allowlisted_sourced_file_is_exempt(self, tmp_path: Path) -> None:
        root = _make_repo(
            tmp_path,
            {"scripts/lib/common.sh": "644"},
            allowlist="scripts/lib/common.sh  # sourced library\n",
        )
        assert _run(root).returncode == 0

    def test_out_of_scope_paths_are_ignored(self, tmp_path: Path) -> None:
        root = _make_repo(tmp_path, {"elsewhere/x.sh": "644"})
        assert _run(root).returncode == 0

    def test_repo_itself_is_green(self) -> None:
        proc = subprocess.run(
            ["bash", str(CHECK), str(REPO)], text=True, capture_output=True, check=False
        )
        assert proc.returncode == 0, proc.stderr
