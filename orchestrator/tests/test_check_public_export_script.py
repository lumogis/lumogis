# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Positive rejection harness for ``scripts/check-public-export.sh`` (LUM-242)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
CHECK = REPO / "scripts/check-public-export.sh"
STRIP_LIST = REPO / "scripts" / "public-export-strip-list.txt"

# Canonical OpenAPI CI export contract paths — duplicate exactly scripts/check-public-export.sh
# lum303_paths array + comment block (LUM-303).
LUM303_CANONICAL_OPENAPI_PATHS: tuple[str, ...] = (
    ".github/workflows/ci.yml",
    ".github/scripts/openapi-check-paths.sh",
    ".github/scripts/openapi-breaking-check.sh",
    "Makefile",
    "orchestrator/scripts/dump_openapi.py",
    "clients/lumogis-web/openapi.snapshot.json",
    "clients/lumogis-web/scripts/codegen.mjs",
    "clients/lumogis-web/package.json",
    "clients/lumogis-web/package-lock.json",
    "scripts/fixtures/openapi-breaking-check/base.json",
    "scripts/fixtures/openapi-breaking-check/after-compatible.json",
    "scripts/fixtures/openapi-breaking-check/after-breaking.json",
)


def _minimal_license() -> str:
    return """SPDX-License-Identifier: AGPL-3.0-only
Copyright (C) Example

This program is free software licensed under AGPL-3.0-only.
"""


def _lum303_minimal_ci_yml_with_job() -> str:
    return """jobs:
  openapi-check:
    runs-on: ubuntu-latest
    steps:
      - run: echo ok
"""


def _lum303_minimal_ci_yml_missing_job() -> str:
    return """jobs:
  other-job:
    runs-on: ubuntu-latest
    steps:
      - run: echo ok
"""


def _lum303_minimal_ci_yml_job_only_in_comment() -> str:
    return """jobs:
  #  openapi-check: was removed
  other-job:
    runs-on: ubuntu-latest
    steps:
      - run: echo ok
"""


def _lum303_minimal_ci_yml_wrong_indent_job() -> str:
    return """jobs:
    openapi-check:
    runs-on: ubuntu-latest
"""


def _minimal_passing_export_openapi_contract(tmp_path: Path) -> None:
    """Materialise LICENSE + all LUM-303 paths with secret-scan-safe stub content."""
    (tmp_path / "LICENSE").write_text(_minimal_license(), encoding="utf-8")
    for rel in LUM303_CANONICAL_OPENAPI_PATHS:
        full = tmp_path / rel
        full.parent.mkdir(parents=True, exist_ok=True)
        if rel.endswith("ci.yml"):
            full.write_text(_lum303_minimal_ci_yml_with_job(), encoding="utf-8")
        elif rel.endswith(".sh"):
            full.write_text("#!/usr/bin/env bash\necho stub\n", encoding="utf-8")
        elif rel == "Makefile":
            full.write_text("all:\n\t@true\n", encoding="utf-8")
        elif rel.endswith(".py"):
            full.write_text(
                "# SPDX-License-Identifier: AGPL-3.0-only\nprint('stub')\n",
                encoding="utf-8",
            )
        elif rel.endswith("openapi.snapshot.json"):
            full.write_text('{"openapi": "3.0.0", "info": {"title": "stub"}}\n', encoding="utf-8")
        elif rel.endswith("codegen.mjs"):
            full.write_text("// stub codegen\nexport {};\n", encoding="utf-8")
        elif rel.endswith("package.json"):
            full.write_text(
                '{"name":"stub","version":"0.0.0","private":true}\n',
                encoding="utf-8",
            )
        elif rel.endswith("package-lock.json"):
            full.write_text('{"name":"stub","lockfileVersion":3,"packages":{}}\n', encoding="utf-8")
        elif rel.endswith(".json"):
            full.write_text("{}\n", encoding="utf-8")
        else:
            full.write_text("stub\n", encoding="utf-8")


def _run_check(tmp_path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(CHECK), str(tmp_path)],
        capture_output=True,
        text=True,
        cwd=str(REPO),
    )


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


def test_lum303_minimal_export_passes_openapi_contract(tmp_path):
    _minimal_passing_export_openapi_contract(tmp_path)
    proc = _run_check(tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr


@pytest.mark.parametrize("missing", LUM303_CANONICAL_OPENAPI_PATHS)
def test_lum303_fails_when_canonical_path_missing(tmp_path, missing: str):
    _minimal_passing_export_openapi_contract(tmp_path)
    rel = Path(missing)
    (tmp_path / rel).unlink()
    proc = _run_check(tmp_path)
    combined = proc.stdout + proc.stderr
    assert proc.returncode != 0
    assert "openapi-ci-export-contract" in combined
    assert "LUM-303" in combined


def test_lum303_fails_when_openapi_check_job_line_missing(tmp_path):
    _minimal_passing_export_openapi_contract(tmp_path)
    ciyml = tmp_path / ".github" / "workflows" / "ci.yml"
    ciyml.write_text(_lum303_minimal_ci_yml_missing_job(), encoding="utf-8")
    proc = _run_check(tmp_path)
    combined = proc.stdout + proc.stderr
    assert proc.returncode != 0
    assert "openapi-ci-export-contract" in combined
    assert "LUM-303" in combined


def test_lum303_fails_when_openapi_check_only_in_yaml_comment(tmp_path):
    _minimal_passing_export_openapi_contract(tmp_path)
    ciyml = tmp_path / ".github" / "workflows" / "ci.yml"
    ciyml.write_text(_lum303_minimal_ci_yml_job_only_in_comment(), encoding="utf-8")
    proc = _run_check(tmp_path)
    combined = proc.stdout + proc.stderr
    assert proc.returncode != 0
    assert "openapi-ci-export-contract" in combined
    assert "LUM-303" in combined


def test_lum303_fails_when_openapi_check_wrong_indent(tmp_path):
    _minimal_passing_export_openapi_contract(tmp_path)
    ciyml = tmp_path / ".github" / "workflows" / "ci.yml"
    ciyml.write_text(_lum303_minimal_ci_yml_wrong_indent_job(), encoding="utf-8")
    proc = _run_check(tmp_path)
    combined = proc.stdout + proc.stderr
    assert proc.returncode != 0
    assert "openapi-ci-export-contract" in combined
    assert "LUM-303" in combined


def test_lum303_required_paths_disjoint_from_strip_list():
    text = STRIP_LIST.read_text(encoding="utf-8")
    strip_entries: set[str] = set()
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if line:
            strip_entries.add(line)
    overlap = strip_entries.intersection(LUM303_CANONICAL_OPENAPI_PATHS)
    assert not overlap, f"strip list must not list LUM-303 paths: {overlap}"
