# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Generate docker-compose.override.yml ingest bind fragments for multi-root paths (LUM-401)."""

from __future__ import annotations

import os
import re
from pathlib import Path

import yaml

LUMOGIS_INGEST_BINDS_BEGIN = "# lumogis:ingest-binds-begin"
LUMOGIS_INGEST_BINDS_END = "# lumogis:ingest-binds-end"

_OVERRIDE_FILENAME = "docker-compose.override.yml"
_COMPOSE_OVERRIDE_TAIL = "docker-compose.override.yml"


def extra_container_path(index: int) -> str:
    """Container mount target for ingest_paths[index] when index >= 1."""
    if index < 1:
        raise ValueError("extra_container_path requires index >= 1")
    return f"/data-{index}"


def browse_path_to_bind_source(path: str, *, host_os: str | None = None) -> str:
    """Map in-container browse path to host path for compose bind source (pure string rules)."""
    stripped = path.strip()
    os_key = (host_os if host_os is not None else os.environ.get("HOST_OS", "")).lower()
    if os_key == "windows" and stripped.startswith("/host/"):
        parts = stripped[len("/host/") :].split("/", 1)
        drive = parts[0].upper()
        rest = parts[1] if len(parts) > 1 else ""
        return f"{drive}:/{rest}"
    if stripped == "/host":
        return "/"
    if stripped.startswith("/host/"):
        return "/" + stripped[len("/host/") :]
    return stripped


def build_extra_volume_lines(host_paths: list[str], *, host_os: str | None = None) -> list[str]:
    """Compose volume entries for indices 1..n using translated bind sources."""
    lines: list[str] = []
    for idx in range(1, len(host_paths)):
        bind_source = browse_path_to_bind_source(host_paths[idx], host_os=host_os)
        container = extra_container_path(idx)
        lines.append(f"{bind_source}:{container}:ro")
    return lines


def render_managed_volumes_block(host_paths: list[str], *, host_os: str | None = None) -> str:
    """YAML text for marker-wrapped managed volume lines only."""
    volume_lines = build_extra_volume_lines(host_paths, host_os=host_os)
    if not volume_lines:
        return ""
    parts = [LUMOGIS_INGEST_BINDS_BEGIN]
    for vol in volume_lines:
        parts.append(f"      - {vol}")
    parts.append(LUMOGIS_INGEST_BINDS_END)
    return "\n".join(parts) + "\n"


def _minimal_override_scaffold(managed_block: str) -> str:
    header = (
        "# lumogis: auto-generated ingest bind mounts — edit outside markers only\n"
        "services:\n"
        "  orchestrator:\n"
        "    volumes:\n"
    )
    return header + managed_block


def merge_override_with_ingest_binds(
    existing_yaml: str | None,
    host_paths: list[str],
    *,
    host_os: str | None = None,
) -> str:
    """Text-splice managed binds into override YAML (D16 — no full-file parse→dump)."""
    managed = render_managed_volumes_block(host_paths, host_os=host_os)
    if not managed:
        if existing_yaml and existing_yaml.strip():
            return _strip_managed_block(existing_yaml)
        return ""

    if not existing_yaml or not existing_yaml.strip():
        return _minimal_override_scaffold(managed)

    begin_idx = existing_yaml.find(LUMOGIS_INGEST_BINDS_BEGIN)
    end_idx = existing_yaml.find(LUMOGIS_INGEST_BINDS_END)
    if begin_idx != -1 and end_idx != -1 and end_idx > begin_idx:
        end_line = existing_yaml.find("\n", end_idx)
        if end_line == -1:
            end_line = len(existing_yaml)
        else:
            end_line += 1
        return existing_yaml[:begin_idx] + managed + existing_yaml[end_line:]

    # No markers — append managed block under orchestrator.volumes if present, else scaffold.
    if re.search(r"^\s*services:\s*$", existing_yaml, re.MULTILINE):
        if re.search(r"^\s*orchestrator:\s*$", existing_yaml, re.MULTILINE):
            if re.search(r"^\s*volumes:\s*$", existing_yaml, re.MULTILINE):
                return existing_yaml.rstrip() + "\n" + managed
        return (
            existing_yaml.rstrip() + "\n" + _minimal_override_scaffold(managed).split("\n", 1)[-1]
        )

    return _minimal_override_scaffold(managed)


def _strip_managed_block(yaml_text: str) -> str:
    begin_idx = yaml_text.find(LUMOGIS_INGEST_BINDS_BEGIN)
    end_idx = yaml_text.find(LUMOGIS_INGEST_BINDS_END)
    if begin_idx == -1 or end_idx == -1 or end_idx < begin_idx:
        return yaml_text
    end_line = yaml_text.find("\n", end_idx)
    if end_line == -1:
        end_line = len(yaml_text)
    else:
        end_line += 1
    # Include line before begin if it is only whitespace (list item line).
    line_start = yaml_text.rfind("\n", 0, begin_idx)
    if line_start == -1:
        line_start = 0
    else:
        line_start += 1
    before = yaml_text[:line_start]
    after = yaml_text[end_line:]
    return (before + after).rstrip() + ("\n" if (before + after).strip() else "")


def render_operator_snippet(host_paths: list[str], *, host_os: str | None = None) -> str:
    """Human-readable instructions + YAML for manual apply."""
    body = merge_override_with_ingest_binds(None, host_paths, host_os=host_os)
    return (
        "Copy the following into docker-compose.override.yml in your project directory, "
        "then ensure COMPOSE_FILE includes docker-compose.override.yml (colon-separated) "
        "and restart the orchestrator.\n\n"
        f"{body}"
    )


def chain_compose_file_in_env(content: str) -> tuple[str, bool]:
    """Append :docker-compose.override.yml to COMPOSE_FILE if missing."""
    pattern = re.compile(
        r"^[ \t]*COMPOSE_FILE[ \t]*=(.*)$",
        re.MULTILINE,
    )
    match = pattern.search(content)
    if not match:
        if content and not content.endswith("\n"):
            content += "\n"
        return content + "COMPOSE_FILE=docker-compose.yml:" + _COMPOSE_OVERRIDE_TAIL + "\n", True

    value = match.group(1).strip()
    parts = [p.strip() for p in value.split(":") if p.strip()]
    if _COMPOSE_OVERRIDE_TAIL in parts:
        return content, False
    new_value = value + ":" + _COMPOSE_OVERRIDE_TAIL
    new_content = pattern.sub(f"COMPOSE_FILE={new_value}", content, count=1)
    return new_content, True


def unchain_compose_file_in_env(content: str) -> tuple[str, bool]:
    """Remove docker-compose.override.yml from COMPOSE_FILE chain."""
    pattern = re.compile(
        r"^[ \t]*COMPOSE_FILE[ \t]*=(.*)$",
        re.MULTILINE,
    )
    match = pattern.search(content)
    if not match:
        return content, False
    value = match.group(1).strip()
    parts = [p.strip() for p in value.split(":") if p.strip()]
    if _COMPOSE_OVERRIDE_TAIL not in parts:
        return content, False
    parts = [p for p in parts if p != _COMPOSE_OVERRIDE_TAIL]
    new_value = ":".join(parts) if parts else "docker-compose.yml"
    new_content = pattern.sub(f"COMPOSE_FILE={new_value}", content, count=1)
    return new_content, True


def validate_override_structure(
    yaml_text: str,
    *,
    expected_bind_count: int,
) -> tuple[bool, str]:
    """Structural validation only (no docker compose CLI)."""
    if expected_bind_count <= 0:
        if LUMOGIS_INGEST_BINDS_BEGIN in yaml_text or LUMOGIS_INGEST_BINDS_END in yaml_text:
            return False, "unexpected ingest bind markers with zero expected binds"
        return True, ""

    begin = yaml_text.find(LUMOGIS_INGEST_BINDS_BEGIN)
    end = yaml_text.find(LUMOGIS_INGEST_BINDS_END)
    if begin == -1 or end == -1 or end < begin:
        return False, "missing lumogis ingest bind markers"

    slice_text = yaml_text[begin:end]
    bind_lines = [ln for ln in slice_text.splitlines() if ln.strip().startswith("- ") and ":" in ln]
    if len(bind_lines) != expected_bind_count:
        return False, f"expected {expected_bind_count} bind lines, found {len(bind_lines)}"

    for i, ln in enumerate(bind_lines, start=1):
        if f":/data-{i}:ro" not in ln.replace(" ", ""):
            return False, f"bind line {i} missing :/data-{i}:ro target"

    try:
        yaml.safe_load(yaml_text)
    except yaml.YAMLError as exc:
        return False, f"yaml parse error: {exc}"

    return True, ""


def override_path(project_dir: Path | None = None) -> Path:
    root = project_dir if project_dir is not None else Path("/project")
    return root / _OVERRIDE_FILENAME


def project_env_writable(env_file: Path | None = None) -> bool:
    path = env_file if env_file is not None else Path("/project/.env")
    if not path.is_file():
        return False
    parent = path.parent
    return os.access(parent, os.W_OK)


def override_has_managed_binds(yaml_text: str) -> bool:
    return LUMOGIS_INGEST_BINDS_BEGIN in yaml_text and LUMOGIS_INGEST_BINDS_END in yaml_text


def is_effectively_empty_override(yaml_text: str) -> bool:
    stripped = _strip_managed_block(yaml_text).strip()
    if not stripped:
        return True
    try:
        data = yaml.safe_load(stripped)
    except yaml.YAMLError:
        return False
    return data in (None, {})
