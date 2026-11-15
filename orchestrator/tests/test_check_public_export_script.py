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
# Maintainer-only second list (LUM-898). On the main list, so absent from public trees.
STRIP_LIST_LOCAL = REPO / "scripts" / "public-export-strip-list.local.txt"


def _repo_has_git() -> bool:
    """True for normal repos and linked worktrees (.git may be a file)."""
    git_path = REPO / ".git"
    return git_path.is_dir() or git_path.is_file()


def _skip_unless_export_source_repo() -> None:
    """Export-tree rebuild tests need private-main templates (stripped from public snapshot)."""
    if not _repo_has_git():
        pytest.skip("needs git checkout")
    if not (REPO / "docs/public-export").is_dir():
        pytest.skip(
            "docs/public-export absent (public AGPL snapshot; "
            "create-upstream-export-tree runs on private main only)"
        )


# Canonical OpenAPI CI export contract paths — duplicate exactly scripts/check-public-export.sh
# lum303_paths array + comment block (LUM-303).
# Canonical Search overlay CI export contract path — duplicate exactly
# scripts/check-public-export.sh LUM-433 comment block.
SEARCH_OVERLAY_CI_CANONICAL_PATHS: tuple[str, ...] = (".github/workflows/search-overlay-build.yml",)

# The appliance name, tree and make include, assembled from parts: this file ships in
# the public tree, and the LUM-898 gate rejects the literals anywhere in it.
_SERVER_NAME: str = "lumogis-" + "server"
_SERVER_TREE: str = "apps/" + _SERVER_NAME
_SERVER_MK: str = "Makefile." + "server" + ".mk"


def _list_entries(path: Path) -> list[str]:
    """Strip-list entries in file order, comments and blank lines dropped."""
    out: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if line:
            out.append(line)
    return out


def _all_strip_entries() -> set[str]:
    """Main list plus the local list when present, as both export scripts read them."""
    entries = set(_list_entries(STRIP_LIST))
    if STRIP_LIST_LOCAL.is_file():
        entries.update(_list_entries(STRIP_LIST_LOCAL))
    return entries


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
    (tmp_path / "EVALUATION.md").write_text(
        "# SPDX-License-Identifier: AGPL-3.0-only\n"
        "Operator evaluation stub for export contract tests.\n",
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
    # Keyed to a path on the main list, so the case holds in a public checkout too. It
    # was orchestrator/plugins/graph until the knowledge graph left the strip list
    # (LUM-855, ADR 229). Not under apps/, which has its own guard, so this proves the
    # strip-list check itself fails closed.
    (tmp_path / "LICENSE").write_text(_minimal_license(), encoding="utf-8")
    bad = tmp_path / "docs" / "_librarian"
    bad.mkdir(parents=True)

    proc = subprocess.run(
        ["bash", str(CHECK), str(tmp_path)],
        capture_output=True,
        text=True,
        cwd=str(REPO),
    )
    combined = proc.stdout + proc.stderr
    assert proc.returncode != 0
    assert "forbidden path in export candidate: docs/_librarian" in combined


# LUM-855 / ADR 229 — the knowledge graph is AGPL-3.0-only and ships in the public tree.
KG_PUBLIC_PATHS: tuple[str, ...] = (
    "services/lumogis-graph/",
    "orchestrator/plugins/graph/",
    "orchestrator/adapters/falkordb_store.py",
    "orchestrator/services/graph_webhook_dispatcher.py",
    "orchestrator/services/kg_premium_core.py",
    "orchestrator/services/edge_quality.py",
    "docker-compose.falkordb.yml",
    "docker-compose.premium.yml",
    "docker-compose.parity-premium.yml",
    "docker-compose.public-rc-stack.yml",
    "docker-compose.parity.yml",
    "orchestrator/tests/premium/",
    "docs/decisions/007-graph-plugin-architecture.md",
    "docs/decisions/008-graph-provenance-model.md",
    "docs/decisions/009-knowledge_graph_visualization.md",
    "docs/decisions/011-lumogis-graph-service-extraction.md",
    "docs/decisions/035-kg-graph-proxy-service-bearer-fail-closed.md",
)


def test_kg_paths_disjoint_from_strip_list():
    """LUM-855: none of the 17 knowledge-graph paths may return to the strip list."""
    overlap = _all_strip_entries().intersection(KG_PUBLIC_PATHS)
    assert not overlap, f"strip list must not list ADR 229 knowledge-graph paths: {overlap}"


def test_export_tree_includes_knowledge_graph(tmp_path):
    """LUM-855: the export tree ships the knowledge graph, and still passes the gate."""
    _skip_unless_export_source_repo()
    out = tmp_path / "export"
    export_script = REPO / "scripts" / "create-upstream-export-tree.sh"
    proc = subprocess.run(
        ["bash", str(export_script), str(out)],
        capture_output=True,
        text=True,
        cwd=str(REPO),
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    for rel in KG_PUBLIC_PATHS:
        assert (out / rel).exists(), f"{rel} missing from export tree"
    assert (out / "services/lumogis-graph/main.py").is_file()
    assert (out / "orchestrator/plugins/graph/__init__.py").is_file()
    check = _run_check(out)
    assert check.returncode == 0, check.stdout + check.stderr


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
        # A YAML comment, so the file stays parseable: the fail-closed runs-on
        # gate (LUM-855) would otherwise reject it before LUM-433 is reached.
        workflow.read_text(encoding="utf-8") + "# apps/lumogis-hub\n",
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
        # A YAML comment, so the file stays parseable: the fail-closed runs-on
        # gate (LUM-855) would otherwise reject it before LUM-433 is reached.
        # The bare name, not the tree path: the LUM-898 content gate runs first and
        # would reject the tree path before the LUM-433 contract is reached.
        workflow.read_text(encoding="utf-8") + f"# {_SERVER_NAME}\n",
        encoding="utf-8",
    )
    proc = _run_check(tmp_path)
    combined = proc.stdout + proc.stderr
    assert proc.returncode != 0
    assert "search-overlay-ci-export-contract" in combined
    assert "LUM-433" in combined


@pytest.mark.parametrize("needle", [_SERVER_TREE + "/README.md", _SERVER_MK])
def test_check_public_export_rejects_appliance_path_in_content(tmp_path, needle):
    """LUM-898: a public file that names the appliance tree or its make include fails."""
    _minimal_passing_export_search_overlay_contract(tmp_path)
    doc = tmp_path / "docs" / "decisions" / "999-probe.md"
    doc.parent.mkdir(parents=True, exist_ok=True)
    doc.write_text(f"See `{needle}` for details.\n", encoding="utf-8")
    proc = _run_check(tmp_path)
    combined = proc.stdout + proc.stderr
    assert proc.returncode != 0
    assert "docs/decisions/999-probe.md:1" in combined
    assert "LUM-898" in combined


def test_check_public_export_rejects_appliance_path_in_strip_list(tmp_path):
    """LUM-898: no file is exempt, the strip list included (option 2, ADR 254)."""
    _minimal_passing_export_search_overlay_contract(tmp_path)
    strip = tmp_path / "scripts" / "public-export-strip-list.txt"
    strip.parent.mkdir(parents=True, exist_ok=True)
    strip.write_text(_SERVER_TREE + "/\n", encoding="utf-8")
    proc = _run_check(tmp_path)
    combined = proc.stdout + proc.stderr
    assert proc.returncode != 0
    assert "scripts/public-export-strip-list.txt:1" in combined
    assert "LUM-898" in combined


# Nested, not at the root: a root-level copy is on the local strip list, whose guard
# would answer first in a private checkout.
@pytest.mark.parametrize("rel", ["vendor/" + _SERVER_TREE + "/notes.txt", "vendor/" + _SERVER_MK])
def test_check_public_export_rejects_appliance_path_in_file_name(tmp_path, rel):
    """LUM-898: a file whose relative path names the appliance fails, whatever it holds."""
    _minimal_passing_export_search_overlay_contract(tmp_path)
    leaked = tmp_path / rel
    leaked.parent.mkdir(parents=True, exist_ok=True)
    leaked.write_text("nothing to see\n", encoding="utf-8")
    proc = _run_check(tmp_path)
    combined = proc.stdout + proc.stderr
    assert proc.returncode != 0
    assert "appliance path in file name" in combined
    assert "LUM-898" in combined


def test_main_strip_list_is_neutral():
    """LUM-898: the main list ships, so it names no appliance path and lists the local list."""
    data = STRIP_LIST.read_bytes()
    for needle in (_SERVER_TREE, _SERVER_MK):
        assert needle.encode() not in data, "main strip list must not name maintainer-only paths"
    assert "scripts/public-export-strip-list.local.txt" in _list_entries(STRIP_LIST)


def _copy_scripts_without_local_list(tmp_path: Path) -> Path:
    """A fake source checkout: export scripts and main list, no local list."""
    scripts = tmp_path / "src" / "scripts"
    scripts.mkdir(parents=True)
    for name in (
        "check-public-export.sh",
        "create-upstream-export-tree.sh",
        "public-export-strip-list.txt",
        "public-export-personal-tokens.txt",
    ):
        src = REPO / "scripts" / name
        if src.is_file():
            (scripts / name).write_bytes(src.read_bytes())
    return tmp_path / "src"


@pytest.mark.parametrize("marker", [_SERVER_TREE, _SERVER_MK])
@pytest.mark.parametrize("script", ["check-public-export.sh", "create-upstream-export-tree.sh"])
def test_export_scripts_fail_closed_without_local_list(tmp_path, script, marker):
    """LUM-898: maintainer-only paths in the source checkout and no local list dies."""
    src = _copy_scripts_without_local_list(tmp_path)
    if marker == _SERVER_TREE:
        (src / marker).mkdir(parents=True)
    else:
        (src / marker).write_text("\n", encoding="utf-8")
    target = tmp_path / "target"
    target.mkdir()
    proc = subprocess.run(
        ["bash", str(src / "scripts" / script), str(target)],
        capture_output=True,
        text=True,
        cwd=str(src),
    )
    combined = proc.stdout + proc.stderr
    assert proc.returncode != 0, combined
    assert "no local strip list" in combined


def test_check_public_export_runs_without_local_list_in_public_checkout(tmp_path):
    """LUM-898: a public checkout has no local list and no maintainer-only paths; it passes."""
    src = _copy_scripts_without_local_list(tmp_path)
    target = tmp_path / "target"
    target.mkdir()
    _minimal_passing_export_search_overlay_contract(target)
    proc = subprocess.run(
        ["bash", str(src / "scripts" / "check-public-export.sh"), str(target)],
        capture_output=True,
        text=True,
        cwd=str(src),
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_lum492_minimal_export_passes_local_strip_list_contract(tmp_path):
    """LUM-492 / LUM-898: a clean export passes the local strip-list contract."""
    _minimal_passing_export_search_overlay_contract(tmp_path)
    proc = _run_check(tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_lum492_fails_when_local_strip_list_path_present_in_export(tmp_path):
    """LUM-492 / LUM-898: any local-list path present in the export tree fails.

    The local list's paths are also in the combined list that
    ``assert_strip_list_paths_absent`` reads, so that guard rejects the leaked path
    first; the local strip-list contract is the belt-and-suspenders behind it.
    """
    if not STRIP_LIST_LOCAL.is_file():
        pytest.skip("no local strip list (public checkout)")
    entries = _list_entries(STRIP_LIST_LOCAL)
    assert entries, "local strip list is present but empty"
    _minimal_passing_export_search_overlay_contract(tmp_path)
    # A file entry not under apps/, so the generic apps/ guard does not answer first.
    entry = next((e for e in entries if not e.endswith("/") and not e.startswith("apps/")), None)
    assert entry is not None, "local strip list has no file entry outside apps/"
    leaked = tmp_path / entry
    leaked.parent.mkdir(parents=True, exist_ok=True)
    leaked.write_text("probe\n", encoding="utf-8")
    proc = _run_check(tmp_path)
    combined = proc.stdout + proc.stderr
    assert proc.returncode != 0
    assert entry in combined


def test_local_strip_list_is_on_main_list_and_disjoint_from_contracts():
    """LUM-898: the local list never ships and never strips a required public path."""
    assert "scripts/public-export-strip-list.local.txt" in _list_entries(STRIP_LIST)
    if not STRIP_LIST_LOCAL.is_file():
        pytest.skip("no local strip list (public checkout)")
    local = set(_list_entries(STRIP_LIST_LOCAL))
    required = set(LUM303_CANONICAL_OPENAPI_PATHS) | set(SEARCH_OVERLAY_CI_CANONICAL_PATHS)
    assert not local.intersection(required)
    assert not local.intersection(KG_PUBLIC_PATHS)


def test_export_tree_omits_local_strip_list_paths(tmp_path):
    """LUM-492 / LUM-898: no local-list path, nor the local list itself, ships."""
    _skip_unless_export_source_repo()
    out = tmp_path / "export"
    export_script = REPO / "scripts" / "create-upstream-export-tree.sh"
    proc = subprocess.run(
        ["bash", str(export_script), str(out)],
        capture_output=True,
        text=True,
        cwd=str(REPO),
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert not (out / "scripts" / "public-export-strip-list.local.txt").exists()
    if STRIP_LIST_LOCAL.is_file():
        for entry in _list_entries(STRIP_LIST_LOCAL):
            assert not (out / entry).exists(), f"{entry} shipped in the export tree"
    assert not (out / _SERVER_TREE).exists()
    check = _run_check(out)
    assert check.returncode == 0, check.stdout + check.stderr


def test_search_overlay_required_path_disjoint_from_strip_list():
    overlap = _all_strip_entries().intersection(SEARCH_OVERLAY_CI_CANONICAL_PATHS)
    assert not overlap, f"strip list must not list LUM-433 paths: {overlap}"


def test_lum303_required_paths_disjoint_from_strip_list():
    overlap = _all_strip_entries().intersection(LUM303_CANONICAL_OPENAPI_PATHS)
    assert not overlap, f"strip list must not list LUM-303 paths: {overlap}"


def test_export_tree_includes_search_overlay_workflow(tmp_path):
    """LUM-433: Search overlay CI must ship on lumogis/lumogis; Hub CI must not."""
    _skip_unless_export_source_repo()
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


# GHCR publish workflows (ADR 037, amended by ADR 261). They live in lumogis-app so the
# export carries them: a public-only copy was deleted by the export's rsync --delete in
# 0.5.0, and no image was published for five months.
GHCR_PUBLISH_WORKFLOWS: tuple[str, ...] = (
    ".github/workflows/publish-image.yml",
    ".github/workflows/attestation-guard.yml",
    ".github/workflows/attestation-verify.yml",
    ".github/workflows/release-assets.yml",  # LUM-980: attaches the install files
)
_PUBLIC_REPO_GUARD = "github.repository == 'lumogis/lumogis'"


def test_ghcr_publish_workflows_disjoint_from_strip_list():
    overlap = _all_strip_entries().intersection(GHCR_PUBLISH_WORKFLOWS)
    assert not overlap, f"strip list must not list the GHCR publish workflows: {overlap}"


def test_ghcr_publish_workflow_jobs_run_only_in_the_public_repo():
    """ADR 037: one publisher. Every job is guarded, so none runs in lumogis-app."""
    yaml = pytest.importorskip("yaml")
    for rel in GHCR_PUBLISH_WORKFLOWS:
        path = REPO / rel
        assert path.is_file(), f"{rel} is missing; the export would drop it from the public repo"
        jobs = yaml.safe_load(path.read_text(encoding="utf-8"))["jobs"]
        for name, job in jobs.items():
            assert _PUBLIC_REPO_GUARD in str(job.get("if", "")), (
                f"{rel} job {name!r} lacks the {_PUBLIC_REPO_GUARD!r} guard"
            )


def test_export_tree_includes_ghcr_publish_workflows(tmp_path):
    _skip_unless_export_source_repo()
    out = tmp_path / "export"
    export_script = REPO / "scripts" / "create-upstream-export-tree.sh"
    proc = subprocess.run(
        ["bash", str(export_script), str(out)],
        capture_output=True,
        text=True,
        cwd=str(REPO),
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    for rel in GHCR_PUBLISH_WORKFLOWS:
        assert (out / rel).is_file(), f"{rel} missing from the export tree"


def test_export_tree_includes_layered_requirements(tmp_path):
    """LUM-460: public export must ship orchestrator/requirements-core.txt for layered profiles."""
    _skip_unless_export_source_repo()
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
    _skip_unless_export_source_repo()
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
    assert not (out / _SERVER_TREE).exists()
    check = _run_check(out)
    assert check.returncode == 0, check.stdout + check.stderr


def test_export_tree_has_no_apps_subtree(tmp_path):
    """LUM-491: public export must not ship any apps/ subtree."""
    _skip_unless_export_source_repo()
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
    assert not (out / _SERVER_TREE).exists()
    check = _run_check(out)
    assert check.returncode == 0, check.stdout + check.stderr


def test_export_tree_has_sanitized_public_agent_docs(tmp_path):
    """LUM-376: public export substitutes AGENTS + orientation; strips private context pack."""
    _skip_unless_export_source_repo()
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
    assert (out / "EVALUATION.md").is_file()
    evaluation = (out / "EVALUATION.md").read_text(encoding="utf-8")
    evaluation_lower = evaluation.lower()
    assert "lumogis-devtools" not in evaluation
    assert "LUMOGIS_CONTEXT_PACK" not in evaluation
    assert "linear.app" not in evaluation_lower
    assert "githubusercontent" not in evaluation_lower
    check = _run_check(out)
    assert check.returncode == 0, check.stdout + check.stderr


def test_check_public_export_rejects_beginners_leakage(tmp_path):
    """LUM-378: forbidden maintainer token in exported beginners doc fails check."""
    _skip_unless_export_source_repo()
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
