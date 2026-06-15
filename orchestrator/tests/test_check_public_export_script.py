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


def _repo_has_git() -> bool:
    """True for normal repos and linked worktrees (.git may be a file)."""
    git_path = REPO / ".git"
    return git_path.is_dir() or git_path.is_file()


# Canonical OpenAPI CI export contract paths — duplicate exactly scripts/check-public-export.sh
# lum303_paths array + comment block (LUM-303).
# Canonical Search overlay CI export contract path — duplicate exactly
# scripts/check-public-export.sh LUM-433 comment block.
SEARCH_OVERLAY_CI_CANONICAL_PATHS: tuple[str, ...] = (".github/workflows/search-overlay-build.yml",)

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


def _write_minimal_search_overlay_workflow_stub(tmp_path: Path) -> None:
    """Secret-scan-safe stub for LUM-433 export contract (no forbidden Hub substrings)."""
    rel = SEARCH_OVERLAY_CI_CANONICAL_PATHS[0]
    full = tmp_path / rel
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(
        """# SPDX-License-Identifier: AGPL-3.0-only
name: Search overlay build stub
on:
  workflow_dispatch:
jobs:
  build-matrix:
    runs-on: ubuntu-latest
    steps:
      - run: echo stub
""",
        encoding="utf-8",
    )


def _write_minimal_public_agent_docs(tmp_path: Path) -> None:
    """LUM-376 / LUM-378 stubs — avoid forbidden maintainer patterns in export check."""
    (tmp_path / "AGENTS.md").write_text(
        "# SPDX-License-Identifier: AGPL-3.0-only\n"
        "Public agent orientation stub for export contract tests.\n",
        encoding="utf-8",
    )
    (tmp_path / "CONTRIBUTING-BEGINNERS.md").write_text(
        "# SPDX-License-Identifier: AGPL-3.0-only\n"
        "Beginners contributing stub for export contract tests.\n",
        encoding="utf-8",
    )
    orient = tmp_path / "docs" / "LUMOGIS_AGENT_ORIENTATION.md"
    orient.parent.mkdir(parents=True, exist_ok=True)
    orient.write_text(
        "# SPDX-License-Identifier: AGPL-3.0-only\n"
        "Agent orientation stub for export contract tests.\n",
        encoding="utf-8",
    )


def _minimal_passing_export_openapi_contract(
    tmp_path: Path,
    *,
    include_search_overlay: bool = True,
) -> None:
    """Materialise LICENSE + all LUM-303 paths with secret-scan-safe stub content."""
    (tmp_path / "LICENSE").write_text(_minimal_license(), encoding="utf-8")
    _write_minimal_public_agent_docs(tmp_path)
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
    if include_search_overlay:
        _write_minimal_search_overlay_workflow_stub(tmp_path)


def _minimal_passing_export_search_overlay_contract(tmp_path: Path) -> None:
    """LUM-433 minimal tree: OpenAPI contract + Search overlay workflow stub."""
    _minimal_passing_export_openapi_contract(tmp_path, include_search_overlay=True)


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


def test_lum433_minimal_export_passes_with_search_workflow(tmp_path):
    _minimal_passing_export_search_overlay_contract(tmp_path)
    proc = _run_check(tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_lum433_fails_when_search_workflow_missing(tmp_path):
    _minimal_passing_export_openapi_contract(tmp_path, include_search_overlay=False)
    proc = _run_check(tmp_path)
    combined = proc.stdout + proc.stderr
    assert proc.returncode != 0
    assert "search-overlay-ci-export-contract" in combined
    assert "LUM-433" in combined


def test_lum433_fails_when_workflow_references_hub(tmp_path):
    _minimal_passing_export_search_overlay_contract(tmp_path)
    workflow = tmp_path / SEARCH_OVERLAY_CI_CANONICAL_PATHS[0]
    workflow.write_text(
        workflow.read_text(encoding="utf-8") + "apps/lumogis-hub\n",
        encoding="utf-8",
    )
    proc = _run_check(tmp_path)
    combined = proc.stdout + proc.stderr
    assert proc.returncode != 0
    assert "search-overlay-ci-export-contract" in combined
    assert "LUM-433" in combined


def test_lum433_fails_when_workflow_references_server(tmp_path):
    _minimal_passing_export_search_overlay_contract(tmp_path)
    workflow = tmp_path / SEARCH_OVERLAY_CI_CANONICAL_PATHS[0]
    workflow.write_text(
        workflow.read_text(encoding="utf-8") + "apps/lumogis-server\n",
        encoding="utf-8",
    )
    proc = _run_check(tmp_path)
    combined = proc.stdout + proc.stderr
    assert proc.returncode != 0
    assert "search-overlay-ci-export-contract" in combined
    assert "LUM-433" in combined


def test_search_overlay_required_path_disjoint_from_strip_list():
    text = STRIP_LIST.read_text(encoding="utf-8")
    strip_entries: set[str] = set()
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if line:
            strip_entries.add(line)
    overlap = strip_entries.intersection(SEARCH_OVERLAY_CI_CANONICAL_PATHS)
    assert not overlap, f"strip list must not list LUM-433 paths: {overlap}"


def test_lum303_required_paths_disjoint_from_strip_list():
    text = STRIP_LIST.read_text(encoding="utf-8")
    strip_entries: set[str] = set()
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if line:
            strip_entries.add(line)
    overlap = strip_entries.intersection(LUM303_CANONICAL_OPENAPI_PATHS)
    assert not overlap, f"strip list must not list LUM-303 paths: {overlap}"


def test_export_tree_includes_search_overlay_workflow(tmp_path):
    """LUM-433: Search overlay CI must ship on lumogis/lumogis; Hub CI must not."""
    if not _repo_has_git():
        pytest.skip("needs git checkout")
    out = tmp_path / "export"
    export_script = REPO / "scripts" / "create-upstream-export-tree.sh"
    proc = subprocess.run(
        ["bash", str(export_script), str(out)],
        capture_output=True,
        text=True,
        cwd=str(REPO),
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert (out / SEARCH_OVERLAY_CI_CANONICAL_PATHS[0]).is_file()
    assert not (out / ".github/workflows/hub-build.yml").exists()
    check = _run_check(out)
    assert check.returncode == 0, check.stdout + check.stderr


def test_export_tree_includes_layered_requirements(tmp_path):
    """LUM-460: public export must ship orchestrator/requirements-core.txt for layered profiles."""
    if not _repo_has_git():
        pytest.skip("needs git checkout")
    out = tmp_path / "export"
    export_script = REPO / "scripts" / "create-upstream-export-tree.sh"
    proc = subprocess.run(
        ["bash", str(export_script), str(out)],
        capture_output=True,
        text=True,
        cwd=str(REPO),
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    core = out / "orchestrator" / "requirements-core.txt"
    full = out / "orchestrator" / "requirements.txt"
    assert core.is_file(), "orchestrator/requirements-core.txt missing from export tree"
    assert full.is_file()
    assert "-r requirements-core.txt" in full.read_text(encoding="utf-8")
    check = _run_check(out)
    assert check.returncode == 0, check.stdout + check.stderr


def test_export_tree_omits_hub_build_workflow(tmp_path):
    """LUM-329: Hub CI must not ship on lumogis/lumogis (no Hub tree in export)."""
    if not _repo_has_git():
        pytest.skip("needs git checkout")
    out = tmp_path / "export"
    export_script = REPO / "scripts" / "create-upstream-export-tree.sh"
    proc = subprocess.run(
        ["bash", str(export_script), str(out)],
        capture_output=True,
        text=True,
        cwd=str(REPO),
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert not (out / ".github/workflows/hub-build.yml").exists()
    assert not (out / "apps/lumogis-hub").exists()
    assert not (out / "apps/lumogis-server").exists()
    check = _run_check(out)
    assert check.returncode == 0, check.stdout + check.stderr


def test_export_tree_has_no_apps_subtree(tmp_path):
    """LUM-491: public export must not ship any apps/ subtree (private Server leak guard)."""
    if not _repo_has_git():
        pytest.skip("needs git checkout")
    out = tmp_path / "export"
    export_script = REPO / "scripts" / "create-upstream-export-tree.sh"
    proc = subprocess.run(
        ["bash", str(export_script), str(out)],
        capture_output=True,
        text=True,
        cwd=str(REPO),
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    apps_dir = out / "apps"
    if apps_dir.is_dir():
        children = list(apps_dir.iterdir())
        assert children == [], f"export tree must not contain apps/ entries: {children}"
    assert not (out / "apps/lumogis-hub").exists()
    assert not (out / "apps/lumogis-server").exists()
    check = _run_check(out)
    assert check.returncode == 0, check.stdout + check.stderr


def test_export_tree_has_sanitized_public_agent_docs(tmp_path):
    """LUM-376: public export substitutes AGENTS + orientation; strips private context pack."""
    if not _repo_has_git():
        pytest.skip("needs git checkout")
    out = tmp_path / "export"
    export_script = REPO / "scripts" / "create-upstream-export-tree.sh"
    proc = subprocess.run(
        ["bash", str(export_script), str(out)],
        capture_output=True,
        text=True,
        cwd=str(REPO),
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert (out / "AGENTS.md").is_file()
    assert (out / "docs/LUMOGIS_AGENT_ORIENTATION.md").is_file()
    assert not (out / "docs/LUMOGIS_CONTEXT_PACK.md").exists()
    assert not (out / "docs/public-export").exists()
    agents = (out / "AGENTS.md").read_text(encoding="utf-8")
    orientation = (out / "docs/LUMOGIS_AGENT_ORIENTATION.md").read_text(encoding="utf-8")
    combined = agents + orientation
    assert "lumogis-devtools" not in combined
    assert "LUMOGIS_CONTEXT_PACK" not in combined
    assert "linear.app" not in combined.lower()
    contrib = (out / "CONTRIBUTING.md").read_text(encoding="utf-8")
    assert "LUMOGIS_AGENT_ORIENTATION.md" in contrib
    assert "LUMOGIS_CONTEXT_PACK.md" not in contrib
    assert (out / "CONTRIBUTING-BEGINNERS.md").is_file()
    beginners = (out / "CONTRIBUTING-BEGINNERS.md").read_text(encoding="utf-8")
    beginners_lower = beginners.lower()
    assert "lumogis-devtools" not in beginners
    assert "LUMOGIS_CONTEXT_PACK" not in beginners
    assert "linear.app" not in beginners_lower
    assert "githubusercontent" not in beginners_lower
    check = _run_check(out)
    assert check.returncode == 0, check.stdout + check.stderr


def test_check_public_export_rejects_beginners_leakage(tmp_path):
    """LUM-378: forbidden maintainer token in exported beginners doc fails check."""
    if not _repo_has_git():
        pytest.skip("needs git checkout")
    out = tmp_path / "export"
    export_script = REPO / "scripts" / "create-upstream-export-tree.sh"
    proc = subprocess.run(
        ["bash", str(export_script), str(out)],
        capture_output=True,
        text=True,
        cwd=str(REPO),
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    beginners = out / "CONTRIBUTING-BEGINNERS.md"
    assert beginners.is_file()
    beginners.write_text(
        beginners.read_text(encoding="utf-8") + "\nLUMOGIS_CONTEXT_PACK\n",
        encoding="utf-8",
    )
    check = _run_check(out)
    assert check.returncode != 0, check.stdout + check.stderr
    combined = (check.stdout + check.stderr).lower()
    assert "lum-378" in combined or "beginners" in combined
