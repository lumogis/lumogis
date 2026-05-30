# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Tests for compose_ingest_binds (LUM-401)."""

from __future__ import annotations

from unittest.mock import patch

from compose_ingest_binds import LUMOGIS_INGEST_BINDS_BEGIN
from compose_ingest_binds import LUMOGIS_INGEST_BINDS_END
from compose_ingest_binds import browse_path_to_bind_source
from compose_ingest_binds import chain_compose_file_in_env
from compose_ingest_binds import extra_container_path
from compose_ingest_binds import merge_override_with_ingest_binds
from compose_ingest_binds import render_operator_snippet
from compose_ingest_binds import unchain_compose_file_in_env
from compose_ingest_binds import validate_override_structure


def test_extra_container_path_indexing():
    assert extra_container_path(1) == "/data-1"
    assert extra_container_path(2) == "/data-2"


def test_browse_path_to_bind_source_linux():
    assert browse_path_to_bind_source("/host/media/foo") == "/media/foo"


def test_browse_path_to_bind_source_windows():
    assert browse_path_to_bind_source("/host/c/Users/x", host_os="windows") == "C:/Users/x"


def test_browse_path_to_bind_source_does_not_check_mount():
    with patch("pathlib.Path.exists", return_value=False):
        assert browse_path_to_bind_source("/host/x") == "/x"


def test_render_override_yaml_two_binds():
    paths = ["/host/primary", "/host/media/foo", "/host/other"]
    merged = merge_override_with_ingest_binds(None, paths)
    assert "/media/foo:/data-1:ro" in merged.replace(" ", "")
    assert "/other:/data-2:ro" in merged.replace(" ", "")
    assert "/host/media" not in merged.split("ro")[0]


def test_merge_override_preserves_host_os_block():
    existing = """services:
  orchestrator:
    environment:
      HOST_OS: linux
    volumes:
      - /:/host:ro
"""
    paths = ["/host/a", "/host/extra"]
    merged = merge_override_with_ingest_binds(existing, paths)
    assert "HOST_OS: linux" in merged
    assert LUMOGIS_INGEST_BINDS_BEGIN in merged


def test_merge_markers_survive_two_consecutive_puts():
    existing = """services:
  orchestrator:
    environment:
      HOST_OS: linux
    volumes:
      - /:/host:ro
"""
    first = ["/host/a", "/host/one", "/host/two"]
    mid = merge_override_with_ingest_binds(existing, first)
    second = ["/host/a", "/host/three", "/host/four"]
    final = merge_override_with_ingest_binds(mid, second)
    assert LUMOGIS_INGEST_BINDS_BEGIN in final
    assert LUMOGIS_INGEST_BINDS_END in final
    assert "HOST_OS: linux" in final
    assert "/three:/data-1:ro" in final.replace(" ", "")
    assert "/four:/data-2:ro" in final.replace(" ", "")


def test_merge_override_strips_managed_block_when_single_path():
    existing = merge_override_with_ingest_binds(None, ["/host/a", "/host/extra"])
    stripped = merge_override_with_ingest_binds(existing, ["/host/a"])
    assert LUMOGIS_INGEST_BINDS_BEGIN not in stripped


def test_validate_override_structure_rejects_bad_yaml():
    ok, err = validate_override_structure("not: [yaml", expected_bind_count=1)
    assert not ok
    assert "yaml" in err.lower() or "marker" in err.lower()


def test_chain_compose_file_appends_override():
    content, changed = chain_compose_file_in_env("COMPOSE_FILE=docker-compose.yml\n")
    assert changed
    assert "docker-compose.override.yml" in content


def test_chain_compose_file_inserts_when_key_absent():
    content, changed = chain_compose_file_in_env("FOO=bar\n")
    assert changed
    assert content.startswith("FOO=bar")
    assert "COMPOSE_FILE=docker-compose.yml:docker-compose.override.yml" in content


def test_chain_compose_file_idempotent():
    first, c1 = chain_compose_file_in_env("COMPOSE_FILE=docker-compose.yml\n")
    assert c1
    second, c2 = chain_compose_file_in_env(first)
    assert not c2


def test_validation_failure_does_not_chain_compose_file(monkeypatch, tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("COMPOSE_FILE=docker-compose.yml\n")
    override = tmp_path / "docker-compose.override.yml"
    monkeypatch.setattr("routes.admin._PROJECT_ENV_FILE", env_file)
    monkeypatch.setattr("routes.admin.project_env_writable", lambda _f=None: True)
    monkeypatch.setattr("routes.admin.override_path", lambda project_dir=None: override)
    monkeypatch.setattr(
        "routes.admin.validate_override_structure",
        lambda merged, expected_bind_count: (False, "invalid structure"),
    )
    from routes.admin import _apply_compose_ingest_auto_tier

    written, validation_ok = _apply_compose_ingest_auto_tier(["/host/a", "/host/b"])
    assert validation_ok is False
    assert written is False
    assert env_file.read_text() == "COMPOSE_FILE=docker-compose.yml\n"
    assert not override.is_file()


def test_shrink_deletes_empty_override_and_unchains():
    content = "COMPOSE_FILE=docker-compose.yml:docker-compose.override.yml\n"
    new_content, changed = unchain_compose_file_in_env(content)
    assert changed
    assert "override" not in new_content


def test_render_snippet_includes_instructions():
    text = render_operator_snippet(["/host/a", "/host/b"])
    assert "docker-compose.override.yml" in text
    assert LUMOGIS_INGEST_BINDS_BEGIN in text
