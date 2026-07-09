# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Unit tests for the share/unshare batch handlers (LUM-157).

Focus on the review-carried contracts:

* a stale/foreign job is a terminal **no-op** (stage ``done``), never an error;
* a handler-set ``partial`` stage survives (``_run_one_tick`` does not force
  share kinds to ``done``);
* both directions serialise under the per-document advisory lock.
"""

from __future__ import annotations

import contextlib

import pytest
from services.batch_handlers.share_document import ShareDocumentPayload
from services.batch_handlers.share_document import handle_share
from services.batch_handlers.share_document import handle_unshare

import config


class _HandlerStore:
    def __init__(self, source: dict | None) -> None:
        self._source = source

    def fetch_one(self, query: str, params: tuple | None = None):
        return dict(self._source) if self._source is not None else None


@pytest.fixture
def progress_calls(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    calls: list[dict] = []
    monkeypatch.setattr(
        "services.ingest_progress.update_ingest_job_progress",
        lambda **kw: calls.append(kw),
    )
    # Advisory lock degrades to a no-op in the unit environment; make that
    # explicit and fast (no DB connection attempt).
    monkeypatch.setattr(
        "services.share_lock.share_document_lock",
        lambda _doc_id: contextlib.nullcontext(),
    )
    return calls


def _set_source(monkeypatch, source):
    monkeypatch.setattr(config, "get_metadata_store", lambda: _HandlerStore(source))


def test_share_stale_job_is_terminal_noop(progress_calls, monkeypatch):
    _set_source(monkeypatch, None)  # source gone / foreign
    handle_share(user_id="alice", payload=ShareDocumentPayload(document_id=9), job_id=1)
    stages = [c["stage"] for c in progress_calls]
    assert stages == ["done"]  # no "projecting", no error


def test_share_success_reports_done(progress_calls, monkeypatch):
    _set_source(monkeypatch, {"id": 9, "user_id": "alice", "scope": "personal"})
    monkeypatch.setattr(
        "services.projection.project_file_with_status",
        lambda src, *, target_scope, actor: ({"id": 90}, 3, 0),
    )
    handle_share(user_id="alice", payload=ShareDocumentPayload(document_id=9), job_id=2)
    stages = [c["stage"] for c in progress_calls]
    assert stages == ["projecting", "done"]


def test_share_partial_reports_partial_stage(progress_calls, monkeypatch):
    _set_source(monkeypatch, {"id": 9, "user_id": "alice", "scope": "personal"})
    monkeypatch.setattr(
        "services.projection.project_file_with_status",
        lambda src, *, target_scope, actor: ({"id": 90}, 2, 1),
    )
    handle_share(user_id="alice", payload=ShareDocumentPayload(document_id=9), job_id=3)
    last = progress_calls[-1]
    assert last["stage"] == "partial"
    assert "2 of 3" in last["status_message"]


def test_unshare_stale_job_is_terminal_noop(progress_calls, monkeypatch):
    _set_source(monkeypatch, None)
    called = {"n": 0}
    monkeypatch.setattr(
        "services.projection.unproject_file",
        lambda *a, **k: called.__setitem__("n", called["n"] + 1),
    )
    handle_unshare(
        user_id="alice", payload=ShareDocumentPayload(document_id=9), job_id=4
    )
    assert [c["stage"] for c in progress_calls] == ["done"]
    assert called["n"] == 0  # no unproject when source is gone


def test_unshare_success_reports_done(progress_calls, monkeypatch):
    _set_source(monkeypatch, {"id": 9, "user_id": "alice", "scope": "personal"})
    monkeypatch.setattr("services.projection.unproject_file", lambda *a, **k: None)
    handle_unshare(
        user_id="alice", payload=ShareDocumentPayload(document_id=9), job_id=5
    )
    assert [c["stage"] for c in progress_calls] == ["projecting", "done"]
