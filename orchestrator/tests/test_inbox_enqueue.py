# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Unit tests for LUM-330 inbox enqueue / stability / poll fast-path."""

from __future__ import annotations

import json
from datetime import datetime
from datetime import timezone
from pathlib import Path
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

import config
from models.ingest import IngestResult
from services import ingest as ingest_mod


@pytest.fixture
def inbox_tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    workspace = tmp_path / "workspace"
    inbox = workspace / "inbox"
    inbox.mkdir(parents=True)
    (workspace / "quarantine").mkdir()
    monkeypatch.setenv("WORKSPACE_PATH", str(workspace))
    monkeypatch.setenv("LUMOGIS_INBOX_PATH", str(inbox))
    monkeypatch.setenv("LUMOGIS_INBOX_MODE", "event")
    monkeypatch.setenv("INBOX_OWNER_USER_ID", "owner-1")
    return inbox


def test_wait_for_stable_file_when_unchanging_then_true(inbox_tree: Path) -> None:
    f = inbox_tree / "doc.txt"
    f.write_text("hello", encoding="utf-8")
    assert ingest_mod.wait_for_stable_file(f, budget_ms=500) is True


def test_wait_for_stable_file_when_vanished_then_false(inbox_tree: Path) -> None:
    missing = inbox_tree / "gone.txt"
    assert ingest_mod.wait_for_stable_file(missing, budget_ms=200) is False


def test_should_ignore_hidden_and_partial_suffixes() -> None:
    assert ingest_mod._should_ignore_inbox_basename(".hidden.pdf") is True
    assert ingest_mod._should_ignore_inbox_basename("x.tmp") is True
    assert ingest_mod._should_ignore_inbox_basename("x.part") is True
    assert ingest_mod._should_ignore_inbox_basename("x.crdownload") is True
    assert ingest_mod._should_ignore_inbox_basename("ok.pdf") is False


def test_on_moved_dest_not_ignored_when_src_was_tmp(inbox_tree: Path, monkeypatch) -> None:
    called: list[str] = []

    def _fake_enqueue(path, *, user_id, source):
        called.append(str(path))

    monkeypatch.setattr(ingest_mod, "enqueue_inbox_file", _fake_enqueue)
    handler = ingest_mod._InboxHandler(owner_user_id="owner-1")
    event = MagicMock(is_directory=False, dest_path=str(inbox_tree / "a.pdf"))
    handler.on_moved(event)
    assert called == [str(inbox_tree / "a.pdf")]


def test_enqueue_oversize_skips_ingest(inbox_tree: Path, monkeypatch) -> None:
    monkeypatch.setattr(config, "get_inbox_max_file_bytes", lambda: 5)
    f = inbox_tree / "big.txt"
    f.write_bytes(b"x" * 10)
    ingest_mock = MagicMock()
    monkeypatch.setattr(ingest_mod, "ingest_file", ingest_mock)
    ingest_mod.enqueue_inbox_file(f, user_id="owner-1", source="watcher")
    ingest_mock.assert_not_called()


def test_enqueue_containment_rejects_outside_inbox(inbox_tree: Path, monkeypatch) -> None:
    outside = inbox_tree.parent / "outside.txt"
    outside.write_text("nope", encoding="utf-8")
    ingest_mock = MagicMock()
    monkeypatch.setattr(ingest_mod, "ingest_file", ingest_mock)
    ingest_mod.enqueue_inbox_file(outside, user_id="owner-1", source="watcher")
    ingest_mock.assert_not_called()


def test_enqueue_inbox_rejects_symlink_escape(
    inbox_tree: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    outside = inbox_tree.parent / "secret.txt"
    outside.write_text("nope", encoding="utf-8")
    link = inbox_tree / "link.txt"
    link.symlink_to(outside)

    ingest_mock = MagicMock()
    monkeypatch.setattr(ingest_mod, "ingest_file", ingest_mock)
    ingest_mod.enqueue_inbox_file(link, user_id="owner-1", source="watcher")
    ingest_mock.assert_not_called()


def test_ingest_folder_routes_through_safe_ingest_with_containment_root(
    inbox_tree: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = inbox_tree / "doc.txt"
    doc.write_text("hello", encoding="utf-8")
    calls: list[tuple[Path, Path, str]] = []

    def _capture(path, *, user_id, containment_root, source):
        calls.append((Path(path), containment_root, source))
        return IngestResult(file_path=str(path), chunk_count=0, skipped=True)

    monkeypatch.setattr(ingest_mod, "_ingest_file_safe", _capture)
    monkeypatch.setattr(config, "get_extractors", lambda: {".txt": lambda _p: ""})
    stats = ingest_mod.ingest_folder(str(inbox_tree), user_id="owner-1")
    assert stats.total_files == 1
    assert len(calls) == 1
    assert calls[0][1] == inbox_tree.resolve(strict=False)
    assert calls[0][2] == "folder"


def test_ingest_folder_rejects_symlink_escape(
    inbox_tree: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    outside = inbox_tree.parent / "secret.txt"
    outside.write_text("nope", encoding="utf-8")
    link = inbox_tree / "link.txt"
    link.symlink_to(outside)

    ingest_mock = MagicMock()
    monkeypatch.setattr(ingest_mod, "ingest_file", ingest_mock)
    monkeypatch.setattr(ingest_mod, "wait_for_stable_file", lambda *_a, **_k: True)
    monkeypatch.setattr(config, "get_extractors", lambda: {".txt": lambda _p: "text"})
    stats = ingest_mod.ingest_folder(str(inbox_tree), user_id="owner-1")
    ingest_mock.assert_not_called()
    assert stats.total_files == 1
    assert stats.skipped == 1


def test_inbox_poll_should_ingest_when_no_row_then_true(
    inbox_tree: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    f = inbox_tree / "new.txt"
    f.write_text("data", encoding="utf-8")
    meta = MagicMock()
    meta.fetch_one.return_value = None
    monkeypatch.setattr(config, "get_metadata_store", lambda: meta)
    assert ingest_mod.inbox_poll_should_ingest(f, user_id="owner-1") is True


def test_inbox_poll_should_ingest_when_mtime_unchanged_then_false(
    inbox_tree: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    f = inbox_tree / "indexed.txt"
    f.write_text("data", encoding="utf-8")
    st = f.stat()
    from datetime import datetime
    from datetime import timezone

    updated = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc)
    meta = MagicMock()
    meta.fetch_one.return_value = {"updated_at": updated}
    monkeypatch.setattr(config, "get_metadata_store", lambda: meta)
    assert ingest_mod.inbox_poll_should_ingest(f, user_id="owner-1") is False


def test_inbox_poll_mtime_fast_path_skips_file_hash_on_second_tick(
    inbox_tree: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    f = inbox_tree / "indexed.txt"
    f.write_text("data", encoding="utf-8")
    st = f.stat()
    indexed_at = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc)
    poll_passes = 0

    def fetch_one(sql: str, params: tuple[object, ...]) -> dict | None:
        nonlocal poll_passes
        if "updated_at" in sql:
            if poll_passes == 0:
                poll_passes += 1
                return None
            return {"updated_at": indexed_at}
        if "file_hash" in sql:
            return None
        return None

    meta = MagicMock()
    meta.fetch_one.side_effect = fetch_one
    monkeypatch.setattr(config, "get_metadata_store", lambda: meta)
    monkeypatch.setattr(ingest_mod, "wait_for_stable_file", lambda *_a, **_k: True)
    monkeypatch.setattr(
        config,
        "get_extractors",
        lambda: {".txt": lambda _p: ""},
    )

    with patch.object(ingest_mod, "_file_hash", wraps=ingest_mod._file_hash) as hash_spy:
        ingest_mod._run_inbox_poll()
        assert hash_spy.call_count == 1
        ingest_mod._run_inbox_poll()
        assert hash_spy.call_count == 1


def test_get_inbox_mode_malformed_coerces_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LUMOGIS_INBOX_MODE", "not-a-mode")
    assert config.get_inbox_mode() == "off"


def test_transient_failure_leaves_file_in_inbox(
    inbox_tree: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    f = inbox_tree / "retry.txt"
    f.write_text("x", encoding="utf-8")

    def _boom(*_a, **_k):
        raise ConnectionError("db down")

    monkeypatch.setattr(ingest_mod, "ingest_file", _boom)
    ingest_mod.enqueue_inbox_file(f, user_id="owner-1", source="watcher")
    assert f.exists()


def test_quarantine_sidecar_shape(inbox_tree: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    f = inbox_tree / "bad.txt"
    f.write_text("x", encoding="utf-8")

    def _boom(*_a, **_k):
        raise ValueError("extract failed")

    monkeypatch.setattr(ingest_mod, "ingest_file", _boom)
    ingest_mod.enqueue_inbox_file(f, user_id="owner-1", source="watcher")
    qdir = config.get_quarantine_path()
    sidecars = list(qdir.glob("*.error.json"))
    assert len(sidecars) == 1
    data = json.loads(sidecars[0].read_text(encoding="utf-8"))
    for key in ("error", "traceback_summary", "ext", "size_bytes", "user_id", "ts", "source"):
        assert key in data
    assert len(data["error"].encode("utf-8")) <= 512
    assert len(data["traceback_summary"].encode("utf-8")) <= 2048


def test_watcher_status_off_mode_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LUMOGIS_INBOX_MODE", "off")
    status = ingest_mod.watcher_status()
    assert status["inbox_mode"] == "off"
    assert status["inbox_watcher"] == "disabled"
