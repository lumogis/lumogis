# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Unit tests for scripts/update/write_rollback_override.py (LUM-187 rollback pinning)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_WRITE_SCRIPT = _REPO_ROOT / "scripts" / "update" / "write_rollback_override.py"

# Import helpers by adding scripts/update to path (not an installed package).
sys.path.insert(0, str(_WRITE_SCRIPT.parent))
import write_rollback_override as wro  # noqa: E402


def test_parse_state_lines_three_column():
    text = (
        "orchestrator\tghcr.io/lumogis/lumogis-orchestrator:latest\t"
        "ghcr.io/lumogis/lumogis-orchestrator@sha256:abc\n"
        "lumogis-web\tghcr.io/lumogis/lumogis-web:latest\t"
        "ghcr.io/lumogis/lumogis-web@sha256:def\n"
    )
    pairs = wro.parse_state_lines(text)
    assert pairs == [
        ("orchestrator", "ghcr.io/lumogis/lumogis-orchestrator@sha256:abc"),
        ("lumogis-web", "ghcr.io/lumogis/lumogis-web@sha256:def"),
    ]


def test_parse_state_lines_skips_legacy_two_column():
    ref = "ghcr.io/lumogis/lumogis-orchestrator@sha256:abc"
    text = f"{ref}\t{ref}\n"
    assert wro.parse_state_lines(text) == []


def test_render_override_yaml_pins_services_and_resets_build():
    yaml = wro.render_override_yaml(
        [
            ("orchestrator", "ghcr.io/lumogis/lumogis-orchestrator@sha256:abc"),
            ("lumogis-web", "ghcr.io/lumogis/lumogis-web@sha256:def"),
        ]
    )
    assert "services:" in yaml
    assert "  orchestrator:" in yaml
    assert "    image: ghcr.io/lumogis/lumogis-orchestrator@sha256:abc" in yaml
    assert "    build: !reset null" in yaml
    assert "  lumogis-web:" in yaml
    assert "    image: ghcr.io/lumogis/lumogis-web@sha256:def" in yaml


def test_write_rollback_override_round_trip(tmp_path: Path):
    state = tmp_path / "previous-images.txt"
    out = tmp_path / "rollback-compose.override.yml"
    state.write_text(
        "orchestrator\timg:tag\tghcr.io/lumogis/lumogis-orchestrator@sha256:deadbeef\n",
        encoding="utf-8",
    )
    assert wro.write_rollback_override(state, out) == 0
    assert "ghcr.io/lumogis/lumogis-orchestrator@sha256:deadbeef" in out.read_text(encoding="utf-8")


def test_write_rollback_override_empty_state_returns_error(tmp_path: Path):
    state = tmp_path / "empty.txt"
    out = tmp_path / "out.yml"
    state.write_text("", encoding="utf-8")
    assert wro.write_rollback_override(state, out) == 1


def test_cli_writes_override(tmp_path: Path):
    state = tmp_path / "state.tsv"
    out = tmp_path / "override.yml"
    state.write_text(
        "postgres\tpostgres:16.8\tpostgres:16.8@sha256:pgdigest\n",
        encoding="utf-8",
    )
    proc = subprocess.run(
        [sys.executable, str(_WRITE_SCRIPT), str(state), str(out)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0
    body = out.read_text(encoding="utf-8")
    assert "postgres:16.8@sha256:pgdigest" in body
