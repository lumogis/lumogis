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


# ---------------------------------------------------------------------------
# LUM-892 — rolling back past the release keeps captures in the workspace
# ---------------------------------------------------------------------------

_ORCH = "orchestrator\timg:tag\tghcr.io/lumogis/lumogis-orchestrator@sha256:abc\n"
_WEB = "lumogis-web\timg:tag\tghcr.io/lumogis/lumogis-web@sha256:def\n"
_CAPTURE_LINE = "      CAPTURE_MEDIA_ROOT: /workspace/captures"


def _override(tmp_path: Path, state_text: str, env_text: str | None) -> str:
    state = tmp_path / "state.tsv"
    out = tmp_path / "override.yml"
    state.write_text(state_text, encoding="utf-8")
    env_file = None
    if env_text is not None:
        env_file = tmp_path / ".env"
        env_file.write_text(env_text, encoding="utf-8")
    elif env_text is None:
        env_file = tmp_path / "missing.env"  # does not exist
    assert wro.write_rollback_override(state, out, env_file=env_file) == 0
    return out.read_text(encoding="utf-8")


def test_override_pins_capture_root_when_env_sets_neither_variable(tmp_path: Path):
    body = _override(tmp_path, _ORCH + _WEB, "POSTGRES_PASSWORD=x\n# CAPTURE_MEDIA_ROOT=/old\n")
    assert "    environment:" in body
    assert _CAPTURE_LINE in body
    # only the orchestrator gets it
    assert body.count("CAPTURE_MEDIA_ROOT") == 1


def test_override_leaves_capture_root_alone_when_lumogis_data_dir_is_set(tmp_path: Path):
    body = _override(tmp_path, _ORCH, "LUMOGIS_DATA_DIR=/x\n")
    assert "CAPTURE_MEDIA_ROOT" not in body


def test_override_leaves_capture_root_alone_when_capture_media_root_is_set(tmp_path: Path):
    body = _override(tmp_path, _ORCH, 'CAPTURE_MEDIA_ROOT="/mnt/caps"\n')
    assert "CAPTURE_MEDIA_ROOT" not in body


def test_override_pins_capture_root_when_variable_is_blank(tmp_path: Path):
    body = _override(tmp_path, _ORCH, "CAPTURE_MEDIA_ROOT=\nLUMOGIS_DATA_DIR=  \n")
    assert _CAPTURE_LINE in body


def test_override_has_no_capture_root_when_orchestrator_is_not_pinned(tmp_path: Path):
    body = _override(tmp_path, _WEB, "")
    assert "CAPTURE_MEDIA_ROOT" not in body


def test_override_pins_capture_root_when_env_file_is_missing(tmp_path: Path):
    body = _override(tmp_path, _ORCH, None)
    assert _CAPTURE_LINE in body


def test_cli_env_file_option(tmp_path: Path):
    state = tmp_path / "state.tsv"
    out = tmp_path / "override.yml"
    env = tmp_path / ".env"
    state.write_text(_ORCH, encoding="utf-8")
    env.write_text("FOO=bar\n", encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(_WRITE_SCRIPT), str(state), str(out), "--env-file", str(env)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0
    assert _CAPTURE_LINE in out.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# LUM-922 — rolling back past the release keeps the stop-entities list
# ---------------------------------------------------------------------------

_STOP_LINE = "      STOP_ENTITIES_PATH: /workspace/config/stop_entities.txt"


def test_override_pins_stop_entities_path_when_env_does_not_set_it(tmp_path: Path):
    body = _override(tmp_path, _ORCH + _WEB, "POSTGRES_PASSWORD=x\n# STOP_ENTITIES_PATH=/old\n")
    assert _STOP_LINE in body
    assert _CAPTURE_LINE in body
    assert body.count("STOP_ENTITIES_PATH") == 1
    assert body.count("    environment:") == 1


def test_override_leaves_stop_entities_path_alone_when_set(tmp_path: Path):
    body = _override(tmp_path, _ORCH, "STOP_ENTITIES_PATH=/mnt/kg/stop.txt\n")
    assert "STOP_ENTITIES_PATH" not in body
    assert _CAPTURE_LINE in body


def test_override_pins_only_stop_entities_when_capture_root_is_set(tmp_path: Path):
    body = _override(tmp_path, _ORCH, "CAPTURE_MEDIA_ROOT=/mnt/caps\n")
    assert "CAPTURE_MEDIA_ROOT" not in body
    assert "    environment:" in body
    assert _STOP_LINE in body


def test_override_pins_stop_entities_path_when_env_file_is_missing(tmp_path: Path):
    assert _STOP_LINE in _override(tmp_path, _ORCH, None)


def test_override_stop_entities_pin_matches_the_default_path(monkeypatch):
    """The pin is where the current code writes the list under the compose mount."""
    import config

    monkeypatch.delenv("STOP_ENTITIES_PATH", raising=False)
    monkeypatch.setenv("WORKSPACE_PATH", "/workspace")
    assert _STOP_LINE.split(": ", 1)[1] == config.get_stop_entities_path()
