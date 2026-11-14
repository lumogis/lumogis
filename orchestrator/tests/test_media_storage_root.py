# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""LUM-892 — where capture media lives by default.

Captures used to default to ``/opt/lumogis/data/captures``, a path no compose
file mounts, so every container recreate deleted them. The default is now
``{WORKSPACE_PATH}/captures``, next to uploads and the inbox.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from services import media_storage


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in ("CAPTURE_MEDIA_ROOT", "LUMOGIS_DATA_DIR", "WORKSPACE_PATH"):
        monkeypatch.delenv(name, raising=False)


def test_default_root_is_workspace_captures(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_PATH", str(tmp_path))
    assert media_storage._resolve_capture_media_root() == tmp_path.resolve() / "captures"


def test_capture_media_root_env_wins(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_PATH", str(tmp_path / "ws"))
    monkeypatch.setenv("LUMOGIS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("CAPTURE_MEDIA_ROOT", str(tmp_path / "explicit"))
    assert media_storage._resolve_capture_media_root() == tmp_path / "explicit"


def test_lumogis_data_dir_wins_over_workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_PATH", str(tmp_path / "ws"))
    monkeypatch.setenv("LUMOGIS_DATA_DIR", "/x")
    assert media_storage._resolve_capture_media_root() == Path("/x/captures")


def test_blank_env_values_are_ignored(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_PATH", str(tmp_path))
    monkeypatch.setenv("CAPTURE_MEDIA_ROOT", "  ")
    monkeypatch.setenv("LUMOGIS_DATA_DIR", "")
    assert media_storage._resolve_capture_media_root() == tmp_path.resolve() / "captures"


def test_existing_storage_key_resolves_under_new_root(tmp_path):
    """``storage_key`` is relative, so moving the root needs no DB rewrite."""
    root = tmp_path / "captures"
    key = "u/c/a/blob.jpg"
    (root / "u/c/a").mkdir(parents=True)
    (root / key).write_bytes(b"jpeg")
    assert media_storage.resolve_storage_key_file(key, root=root).read_bytes() == b"jpeg"


def test_capture_media_root_returns_module_constant(tmp_path, monkeypatch):
    monkeypatch.setattr(media_storage, "_CAPTURE_MEDIA_ROOT", tmp_path)
    assert media_storage.capture_media_root() == tmp_path


# ---------------------------------------------------------------------------
# config.resolve_uploads_path — the pure twin of get_uploads_path (no mkdir)
# ---------------------------------------------------------------------------


def test_resolve_uploads_path_does_not_mkdir(tmp_path, monkeypatch):
    import config

    monkeypatch.delenv("LUMOGIS_UPLOADS_PATH", raising=False)
    monkeypatch.setenv("WORKSPACE_PATH", str(tmp_path))
    resolved = config.resolve_uploads_path()
    assert resolved == tmp_path.resolve() / "uploads"
    assert not resolved.exists()


def test_get_uploads_path_creates_dir(tmp_path, monkeypatch):
    import config

    monkeypatch.delenv("LUMOGIS_UPLOADS_PATH", raising=False)
    monkeypatch.setenv("WORKSPACE_PATH", str(tmp_path))
    assert config.get_uploads_path().is_dir()
