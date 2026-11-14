# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Regression tests: IMAP poll cursor must not advance past failed rows."""

from __future__ import annotations

from datetime import datetime
from datetime import timezone

from models.signals import Signal
from models.signals import SourceConfig
from signals import feed_monitor as fm


def _source() -> SourceConfig:
    return SourceConfig(
        id="src-1",
        name="mail",
        source_type="imap",
        url="imap://h/1/1",
        category="",
        active=True,
        poll_interval=900,
        extraction_method="imap_metadata",
        css_selector_override=None,
        last_polled_at=None,
        last_signal_at=None,
        poll_cursor="100:5",
        user_id="u1",
    )


def _sig(uid: int, uidvalidity: int = 100) -> Signal:
    return Signal(
        signal_id="",
        source_id="src-1",
        title=f"m{uid}",
        url=f"imap://src-1/{uidvalidity}/{uid}",
        published_at=None,
        content_summary="",
        raw_content="",
        entities=[],
        topics=[],
        importance_score=0.0,
        relevance_score=0.0,
        notified=False,
        created_at=datetime.now(timezone.utc),
        user_id="u1",
        source_type="imap",
        category="",
        headers={},
    )


class _FakeAdapter:
    def __init__(self, source, *, client_factory=None):
        self.result_cursor = "100:8"

    def poll(self, *, since=None, reseed_on_uidvalidity_change=False):
        return [_sig(6), _sig(7), _sig(8)]


def test_metadata_poll_cursor_stops_at_process_signal_failure(monkeypatch):
    persisted: dict[str, str] = {}
    processed: list[int] = []

    def fail_on_uid7(raw, user_id=None):
        uid = int(raw.url.rsplit("/", 1)[1])
        processed.append(uid)
        if uid == 7:
            raise RuntimeError("transient db error")

    monkeypatch.setattr("adapters.email_imap_source.EmailImapSource", _FakeAdapter)
    monkeypatch.setattr(fm, "process_signal", fail_on_uid7)
    monkeypatch.setattr(fm, "_is_duplicate", lambda url, user_id: False)
    monkeypatch.setattr(fm, "_fetch_poll_cursor", lambda sid: "100:5")
    monkeypatch.setattr(fm, "_update_poll_timestamp", lambda s: None)
    monkeypatch.setattr(
        fm,
        "_persist_poll_cursor",
        lambda sid, cur: persisted.update({sid: cur}),
    )

    fm._poll_email_source(_source())

    assert processed == [6, 7]
    assert persisted == {"src-1": "100:6"}


def test_metadata_poll_cursor_advances_through_duplicates(monkeypatch):
    persisted: dict[str, str] = {}

    monkeypatch.setattr("adapters.email_imap_source.EmailImapSource", _FakeAdapter)
    monkeypatch.setattr(fm, "process_signal", lambda raw, user_id=None: None)
    monkeypatch.setattr(
        fm,
        "_is_duplicate",
        lambda url, user_id: url.endswith("/7"),
    )
    monkeypatch.setattr(fm, "_fetch_poll_cursor", lambda sid: "100:5")
    monkeypatch.setattr(fm, "_update_poll_timestamp", lambda s: None)
    monkeypatch.setattr(
        fm,
        "_persist_poll_cursor",
        lambda sid, cur: persisted.update({sid: cur}),
    )

    fm._poll_email_source(_source())

    assert persisted == {"src-1": "100:8"}
