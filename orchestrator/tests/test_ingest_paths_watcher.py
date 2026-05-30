# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""LUM-397 C3 — ingest path filesystem watchers."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import config
from services import ingest as ingest_mod


@pytest.fixture
def ingest_watch_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[Path, Path]:
    inbox = tmp_path / "inbox"
    data = tmp_path / "data"
    inbox.mkdir()
    data.mkdir()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "quarantine").mkdir()
    monkeypatch.setenv("WORKSPACE_PATH", str(workspace))
    monkeypatch.setenv("LUMOGIS_INBOX_PATH", str(inbox))
    monkeypatch.setenv("LUMOGIS_INBOX_MODE", "event")
    monkeypatch.setenv("INBOX_OWNER_USER_ID", "owner-1")
    monkeypatch.setenv("INGEST_PATHS", json.dumps([str(data)]))
    monkeypatch.setenv("INGEST_PATHS_WATCH_MODE", "event")
    return inbox, data


def test_ingest_path_handler_rejects_symlink_escape(
    ingest_watch_env: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    _inbox, data = ingest_watch_env
    outside = data.parent / "secret.txt"
    outside.write_text("nope", encoding="utf-8")
    link = data / "link.txt"
    link.symlink_to(outside)

    called: list[str] = []

    def _capture(path, *, user_id):
        called.append(str(path))

    monkeypatch.setattr(ingest_mod, "enqueue_ingest_watch_file", _capture)
    handler = ingest_mod._IngestPathHandler(owner_user_id="owner-1", root=data)
    handler._handle_path(str(link))
    assert called == []


def test_ingest_path_handler_enqueues_supported_file(
    ingest_watch_env: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    _inbox, data = ingest_watch_env
    doc = data / "report.txt"
    doc.write_text("hello", encoding="utf-8")

    called: list[str] = []

    def _capture(path, *, user_id):
        called.append(str(path))

    monkeypatch.setattr(ingest_mod, "enqueue_ingest_watch_file", _capture)
    handler = ingest_mod._IngestPathHandler(owner_user_id="owner-1", root=data)
    handler._handle_path(str(doc))
    assert called == [str(doc.resolve(strict=False))]


def test_ingest_paths_observer_does_not_break_inbox(
    ingest_watch_env: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    real_filesystem_watchers,
) -> None:
    inbox, data = ingest_watch_env
    folder_jobs: list[str] = []
    file_jobs: list[str] = []

    def _fake_enqueue(*, user_id, kind, payload):
        if kind == "ingest_folder":
            folder_jobs.append(payload["path"])
        elif kind == "ingest_watch_file":
            file_jobs.append(payload["path"])

    monkeypatch.setattr("services.batch_queue.enqueue", _fake_enqueue)
    ingest_mod.stop_ingest_path_watchers()
    ingest_mod.stop_watcher()
    ingest_mod.start_watcher(inbox_path=str(inbox))
    ingest_mod.start_ingest_path_watchers()
    try:
        assert ingest_mod._observer is not None
        assert ingest_mod._observer.is_alive()
        assert ingest_mod._ingest_paths_observer is not None
        assert ingest_mod._ingest_paths_observer.is_alive()
        assert str(data.resolve(strict=False)) in folder_jobs
    finally:
        ingest_mod.stop_ingest_path_watchers()
        ingest_mod.stop_watcher()


def test_watcher_status_ingest_paths_off_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("INGEST_PATHS_WATCH_MODE", "off")
    monkeypatch.setenv("INBOX_OWNER_USER_ID", "owner-1")
    status = ingest_mod.watcher_status()
    assert status["ingest_paths_watch"] == "off"
    assert status["ingest_paths_watch_roots"] == "0"


def test_get_ingest_paths_owner_falls_back_to_inbox_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("INGEST_PATHS_OWNER_USER_ID", raising=False)
    monkeypatch.setenv("INBOX_OWNER_USER_ID", "inbox-owner")
    assert config.get_ingest_paths_owner_user_id() == "inbox-owner"


def test_get_ingest_paths_watch_enabled_requires_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("INGEST_PATHS_WATCH_MODE", "event")
    monkeypatch.delenv("INGEST_PATHS_OWNER_USER_ID", raising=False)
    monkeypatch.delenv("INBOX_OWNER_USER_ID", raising=False)
    assert config.get_ingest_paths_watch_enabled() is False
