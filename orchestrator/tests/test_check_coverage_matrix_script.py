# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Harness for ``scripts/check-coverage-matrix.mjs`` (LUM-429)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
CHECK = REPO / "scripts" / "check-coverage-matrix.mjs"
CATALOG = REPO / "scripts" / "feature-ids.json"
FIXTURES = REPO / "scripts" / "tests" / "fixtures" / "coverage-matrix"


def _run(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["node", str(CHECK), *args],
        cwd=cwd or REPO,
        text=True,
        capture_output=True,
        check=False,
    )


@pytest.mark.skipif(
    subprocess.run(["node", "--version"], capture_output=True).returncode != 0,
    reason="node not available",
)
def test_check_coverage_matrix_passes_on_repo_catalog() -> None:
    assert CATALOG.is_file(), "run: node scripts/check-coverage-matrix.mjs --write-catalog"
    proc = _run()
    assert proc.returncode == 0, proc.stdout + proc.stderr


@pytest.mark.skipif(
    subprocess.run(["node", "--version"], capture_output=True).returncode != 0,
    reason="node not available",
)
def test_fixture_matrix_valid_with_catalog() -> None:
    matrix = FIXTURES / "valid-mini.md"
    catalog = FIXTURES / "valid-mini-catalog.json"
    proc = _run(
        "--matrix",
        str(matrix),
        "--catalog",
        str(catalog),
        "--skip-audit-header",
    )
    assert proc.returncode == 0, proc.stderr


@pytest.mark.skipif(
    subprocess.run(["node", "--version"], capture_output=True).returncode != 0,
    reason="node not available",
)
def test_fixture_rejects_invalid_status() -> None:
    proc = _run(
        "--matrix",
        str(FIXTURES / "bad-status.md"),
        "--catalog",
        str(FIXTURES / "valid-mini-catalog.json"),
        "--skip-audit-header",
    )
    assert proc.returncode != 0
    assert "invalid status" in proc.stderr


@pytest.mark.skipif(
    subprocess.run(["node", "--version"], capture_output=True).returncode != 0,
    reason="node not available",
)
def test_fixture_rejects_manual_without_ms_tbd() -> None:
    proc = _run(
        "--matrix",
        str(FIXTURES / "bad-manual.md"),
        "--catalog",
        str(FIXTURES / "valid-mini-catalog.json"),
        "--skip-audit-header",
    )
    assert proc.returncode != 0
    assert "MS-TBD" in proc.stderr or "MS-###" in proc.stderr


@pytest.mark.skipif(
    subprocess.run(["node", "--version"], capture_output=True).returncode != 0,
    reason="node not available",
)
def test_fixture_accepts_ms_numeric_ref() -> None:
    proc = _run(
        "--matrix",
        str(FIXTURES / "valid-mini.md"),
        "--catalog",
        str(FIXTURES / "valid-mini-catalog.json"),
        "--skip-audit-header",
    )
    assert proc.returncode == 0, proc.stderr


@pytest.mark.skipif(
    subprocess.run(["node", "--version"], capture_output=True).returncode != 0,
    reason="node not available",
)
def test_public_layout_skips_private_catalog_ids(tmp_path: Path) -> None:
    """Simulate AGPL export: core/web only; 3.x/4.x catalog IDs are out of scope."""
    audit = "<!-- Last audited: 2026-06-04 fixture -->\n\n"
    core_mini = audit + FIXTURES.joinpath("valid-mini.md").read_text(encoding="utf-8")
    web_mini = core_mini.replace("1.9.", "2.9.")
    (tmp_path / "docs/testing").mkdir(parents=True)
    (tmp_path / "docs/testing/TEST-COVERAGE-MATRIX-core.md").write_text(core_mini, encoding="utf-8")
    (tmp_path / "docs/testing/TEST-COVERAGE-MATRIX-web.md").write_text(web_mini, encoding="utf-8")
    (tmp_path / "scripts").mkdir(parents=True)
    (tmp_path / "scripts/lib").mkdir(parents=True)
    for rel in (
        "scripts/check-coverage-matrix.mjs",
        "scripts/lib/coverage-matrix-parser.mjs",
    ):
        dest = tmp_path / rel
        dest.write_text((REPO / rel).read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "scripts/feature-ids.json").write_text(
        json.dumps(
            {
                "schema": "lumogis-feature-ids/v1",
                "ids": ["1.9.1", "1.9.2", "2.9.1", "2.9.2", "3.1.1", "4.1.1"],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    proc = subprocess.run(
        ["node", "scripts/check-coverage-matrix.mjs"],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert "private matrix file(s) absent" in proc.stdout


@pytest.mark.skipif(
    subprocess.run(["node", "--version"], capture_output=True).returncode != 0,
    reason="node not available",
)
def test_feature_ids_json_matches_four_matrix_prefixes() -> None:
    data = json.loads(CATALOG.read_text(encoding="utf-8"))
    ids = data["ids"]
    assert len(ids) >= 100
    assert data["row_count"] == len(ids)
    for fid in ids:
        assert fid[0] in "1234", fid
    prefixes = {fid.split(".", 1)[0] for fid in ids}
    assert prefixes == {"1", "2", "3", "4"}
