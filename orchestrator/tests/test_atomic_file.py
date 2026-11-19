# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""services._atomic_file.atomic_write_text (LUM-331 Pass 1)."""

from __future__ import annotations

import os
import stat
import threading
from pathlib import Path

import pytest
from services._atomic_file import atomic_write_text

from services import _atomic_file


def _mode(p: Path) -> int:
    return stat.S_IMODE(p.stat().st_mode)


def test_writes_content_and_explicit_mode(tmp_path: Path) -> None:
    f = tmp_path / "state.json"
    atomic_write_text(f, '{"a": 1}', mode=0o600)
    assert f.read_text() == '{"a": 1}'
    assert _mode(f) == 0o600


def test_mode_none_matches_plain_open(tmp_path: Path) -> None:
    """mode=None must behave like open(path, "w"), not like mkstemp's 0600."""
    plain = tmp_path / "plain.txt"
    with open(plain, "w", encoding="utf-8") as fh:
        fh.write("x")
    f = tmp_path / "allow.txt"
    atomic_write_text(f, "x")
    assert _mode(f) == _mode(plain)


def test_replaces_existing_file(tmp_path: Path) -> None:
    f = tmp_path / "home.caddy"
    f.write_text("old")
    atomic_write_text(f, "new", mode=0o640)
    assert f.read_text() == "new"
    assert _mode(f) == 0o640


def test_failure_before_replace_keeps_old_file_and_cleans_temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    f = tmp_path / "state.json"
    f.write_text("previous")

    def boom(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(_atomic_file.os, "replace", boom)
    with pytest.raises(OSError):
        atomic_write_text(f, "new", mode=0o600)
    assert f.read_text() == "previous"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["state.json"]


def test_concurrent_writers_never_leave_partial_content(tmp_path: Path) -> None:
    """Unique temp names: two threads cannot share or clobber a temp file."""
    f = tmp_path / "state.json"
    payloads = [("A" * 50_000), ("B" * 50_000)]
    errors: list[BaseException] = []

    def write(text: str) -> None:
        try:
            for _ in range(20):
                atomic_write_text(f, text, mode=0o600)
        except BaseException as exc:  # pragma: no cover - surfaced below
            errors.append(exc)

    threads = [threading.Thread(target=write, args=(p,)) for p in payloads]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert f.read_text() in payloads
    assert sorted(p.name for p in tmp_path.iterdir()) == ["state.json"]


def test_current_umask_reads_process_umask() -> None:
    old = os.umask(0o027)
    try:
        assert _atomic_file._current_umask() == 0o027
    finally:
        os.umask(old)
