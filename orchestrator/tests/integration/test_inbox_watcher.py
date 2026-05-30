# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Integration-style tests for inbox watcher handler (LUM-330)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from services import ingest as ingest_mod


@pytest.fixture
def inbox_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    workspace = tmp_path / "ws"
    inbox = workspace / "inbox"
    inbox.mkdir(parents=True)
    monkeypatch.setenv("WORKSPACE_PATH", str(workspace))
    monkeypatch.setenv("LUMOGIS_INBOX_PATH", str(inbox))
    monkeypatch.setenv("INBOX_OWNER_USER_ID", "u1")
    return inbox


def test_handler_stable_txt_drop_calls_enqueue(inbox_env: Path, monkeypatch) -> None:
    captured: list[tuple[str, str]] = []

    def _capture(path, *, user_id, source):
        captured.append((str(path), source))

    monkeypatch.setattr(ingest_mod, "enqueue_inbox_file", _capture)
    monkeypatch.setattr(ingest_mod, "wait_for_stable_file", lambda *_a, **_k: True)
    f = inbox_env / "note.txt"
    f.write_text("hello inbox", encoding="utf-8")
    handler = ingest_mod._InboxHandler(owner_user_id="u1")
    event = MagicMock(is_directory=False, src_path=str(f))
    handler.on_created(event)
    assert captured and captured[0][1] == "watcher"


def test_poll_scan_invokes_enqueue_for_new_file(inbox_env: Path, monkeypatch) -> None:
    calls: list[str] = []

    def _capture(path, *, user_id, source):
        calls.append(str(path))

    monkeypatch.setattr(ingest_mod, "enqueue_inbox_file", _capture)
    monkeypatch.setattr(ingest_mod, "inbox_poll_should_ingest", lambda *_a, **_k: True)
    f = inbox_env / "pollme.txt"
    f.write_text("poll", encoding="utf-8")
    ingest_mod._run_inbox_poll()
    assert any("pollme.txt" in c for c in calls)
