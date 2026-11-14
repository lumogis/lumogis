# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""LUM-170 full-ingest tests: the IMAP cursor must NOT be threaded through
``ingest_external_document``'s lexical watermark comparison (which is
ISO-timestamp shaped). Dedup is by ``external_document_id``; the authoritative
IMAP cursor is written by ``_persist_poll_cursor``.
"""

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
        extraction_method="imap_full_ingest",
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
        self.result_cursor = "100:7"

    def poll(self, *, since=None):
        return [_sig(6), _sig(7)]

    def fetch_bodies(self, uids):
        return {u: f"body of {u}" for u in uids}


def test_full_ingest_does_not_pass_uid_cursor_as_watermark(monkeypatch):
    calls = []

    def fake_ingest(**kwargs):
        calls.append(kwargs)
        return None

    persisted = {}

    monkeypatch.setattr("adapters.email_imap_source.EmailImapSource", _FakeAdapter)
    monkeypatch.setattr("services.ingest.ingest_external_document", fake_ingest)
    monkeypatch.setattr(fm, "process_signal", lambda raw, user_id=None: None)
    monkeypatch.setattr(fm, "_is_duplicate", lambda url, user_id: False)
    monkeypatch.setattr(fm, "_fetch_poll_cursor", lambda sid: "100:5")
    monkeypatch.setattr(fm, "_update_poll_timestamp", lambda s: None)
    monkeypatch.setattr(fm, "_persist_poll_cursor", lambda sid, cur: persisted.update({sid: cur}))

    fm._poll_email_ingest_source(_source())

    assert len(calls) == 2, "both message bodies ingested"
    for c in calls:
        assert c["external_kind"] == "email"
        # The dedup key carries UIDVALIDITY:uid ...
        assert c["external_document_id"] in ("100:6", "100:7")
        # ... but the lexical skew guard is DISABLED (stored=None) so the
        # non-monotonic compound cursor can never wrongly skip/advance.
        assert c["stored_source_poll_cursor"] is None
    # Authoritative IMAP cursor persisted from the adapter, not from ingest.
    assert persisted == {"src-1": "100:7"}


def test_full_ingest_attaches_body_for_classification(monkeypatch):
    seen = {}

    monkeypatch.setattr("adapters.email_imap_source.EmailImapSource", _FakeAdapter)
    monkeypatch.setattr("services.ingest.ingest_external_document", lambda **k: None)
    monkeypatch.setattr(fm, "_is_duplicate", lambda url, user_id: False)
    monkeypatch.setattr(fm, "_fetch_poll_cursor", lambda sid: "100:5")
    monkeypatch.setattr(fm, "_update_poll_timestamp", lambda s: None)
    monkeypatch.setattr(fm, "_persist_poll_cursor", lambda sid, cur: None)

    def capture(raw, user_id=None):
        seen[raw.url] = raw.raw_content

    monkeypatch.setattr(fm, "process_signal", capture)

    fm._poll_email_ingest_source(_source())

    assert seen["imap://src-1/100/6"] == "body of 6"
    assert seen["imap://src-1/100/7"] == "body of 7"
