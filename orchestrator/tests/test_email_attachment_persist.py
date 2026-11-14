# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Attachment metadata persistence through the ingest pipeline (LUM-695).

Two regressions live here, both found by reviewing the plan against the code
rather than by running it:

* **Ordering.** ``record_from_headers`` runs BEFORE LUM-697's suppression early
  return (a silenced sender must stay visible in Subscriptions). Attachment
  rows must go AFTER it — the two calls look symmetric and are not. Writing
  them alongside ``record_from_headers`` would create ``email_attachment`` rows
  for mail whose ``signals`` row is never created, orphaning rows from the
  moment a user silences a sender, i.e. for their highest-volume senders.

* **Conflict handling.** ``UNIQUE (user_id, signal_id, part_index)`` plus a
  plain INSERT raises on any reprocessed signal, and ``feed_monitor``'s poll
  loop ``break``s on a handler error — so one duplicate stalls the whole tick,
  not one message.
"""

from __future__ import annotations

from datetime import datetime
from datetime import timezone

import pytest
from models.signals import Signal

import config
from services import email_attachments
from services import signal_processor


class _Store:
    """Records every execute() so the tests can assert on the SQL that ran."""

    def __init__(self, *, raise_on_attachment: Exception | None = None) -> None:
        self.executed: list[tuple[str, tuple]] = []
        self._raise = raise_on_attachment

    def execute(self, sql, params=None):
        if self._raise is not None and "email_attachment" in sql:
            raise self._raise
        self.executed.append((sql, params or ()))
        return None

    def fetch_one(self, sql, params=None):
        return None

    def fetch_all(self, sql, params=None):
        return []

    def attachment_writes(self) -> list[tuple]:
        return [p for sql, p in self.executed if "INSERT INTO email_attachment " in sql]

    def attachment_sql(self) -> list[str]:
        return [sql for sql, _ in self.executed if "INSERT INTO email_attachment " in sql]


def _signal(**kw) -> Signal:
    base = dict(
        signal_id="",
        source_id="src-1",
        title="Invoice",
        url="imap://src-1/100/42",
        published_at=None,
        content_summary="",
        raw_content="body",
        entities=[],
        topics=[],
        importance_score=0.0,
        relevance_score=0.0,
        notified=False,
        created_at=datetime(2026, 7, 25, tzinfo=timezone.utc),
        user_id="u1",
        source_type="imap",
        category="",
        headers={"From": "office@school.edu", "Subject": "Invoice"},
    )
    base.update(kw)
    return Signal(**base)


def _meta(index: int = 0, filename: str = "invoice.pdf") -> email_attachments.AttachmentMeta:
    return email_attachments.AttachmentMeta(
        filename=filename, mime_type="application/pdf", size_bytes=1234, part_index=index
    )


@pytest.fixture
def store(monkeypatch):
    s = _Store()
    monkeypatch.setattr(config, "get_metadata_store", lambda: s)
    return s


@pytest.fixture(autouse=True)
def _quiet_pipeline(monkeypatch):
    """Neutralise everything process_signal does that is not under test."""
    from services import email_unsubscribe

    monkeypatch.setattr(email_unsubscribe, "record_from_headers", lambda **kw: None)
    monkeypatch.setattr(signal_processor, "_notify", lambda sig: False)
    monkeypatch.setattr(signal_processor, "_load_profile", lambda uid: None)
    monkeypatch.setattr(signal_processor.hooks, "fire_background", lambda *a, **k: None)


# ---------------------------------------------------------------------------
# The happy path
# ---------------------------------------------------------------------------


def test_attachment_rows_are_written_for_full_ingest_mail(store, monkeypatch):
    from services import email_sender_rules

    monkeypatch.setattr(email_sender_rules, "should_suppress", lambda *a: False)

    signal_processor.process_signal(
        _signal(attachments=[_meta(0), _meta(1, "photo.png")]), user_id="u1"
    )

    writes = store.attachment_writes()
    assert len(writes) == 2
    assert [w[3] for w in writes] == ["invoice.pdf", "photo.png"], "filename column"
    assert all(w[0] == "u1" for w in writes), "rows are user-scoped"


def test_duplicate_metadata_insert_does_not_raise(store, monkeypatch):
    """Reprocessing a signal must be a no-op, not an IntegrityError.

    ``feed_monitor``'s loop breaks on a handler error, so a raise here stalls the
    entire poll tick rather than skipping one message.
    """
    from services import email_sender_rules

    monkeypatch.setattr(email_sender_rules, "should_suppress", lambda *a: False)

    signal_processor.process_signal(_signal(attachments=[_meta(0)]), user_id="u1")

    sql = store.attachment_sql()
    assert sql, "expected an attachment insert"
    assert "ON CONFLICT" in sql[0].upper()
    assert "DO NOTHING" in sql[0].upper()


def test_a_signal_with_no_attachments_writes_no_rows(store, monkeypatch):
    from services import email_sender_rules

    monkeypatch.setattr(email_sender_rules, "should_suppress", lambda *a: False)

    signal_processor.process_signal(_signal(attachments=[]), user_id="u1")

    assert store.attachment_writes() == []


def test_non_email_signals_never_write_attachment_rows(store, monkeypatch):
    signal_processor.process_signal(
        _signal(source_type="rss", url="https://example.com/x", attachments=[_meta(0)]),
        user_id="u1",
    )

    assert store.attachment_writes() == []


# ---------------------------------------------------------------------------
# The ordering regression
# ---------------------------------------------------------------------------


def test_suppressed_sender_mail_writes_no_attachment_rows(store, monkeypatch):
    """The orphan regression.

    A ``hide`` rule fires → no ``signals`` row AND no ``email_attachment`` rows.
    Rows written next to ``record_from_headers`` (which is deliberately BEFORE
    the suppression return) would orphan for every message from every silenced
    sender.
    """
    from services import email_sender_rules

    monkeypatch.setattr(email_sender_rules, "should_suppress", lambda *a: True)

    signal_processor.process_signal(_signal(attachments=[_meta(0)]), user_id="u1")

    assert store.attachment_writes() == [], "suppressed mail must write no attachment rows"
    signal_writes = [sql for sql, _ in store.executed if "INSERT INTO signals" in sql]
    assert signal_writes == [], "and no signals row either — that is what makes them orphans"


def test_feed_monitor_carries_adapter_metadata_onto_the_signal(monkeypatch):
    """The seam between the adapter's one parse and process_signal's write."""
    from adapters.email_imap_source import BodyAndAttachments
    from models.signals import SourceConfig
    from signals import feed_monitor as fm

    source = SourceConfig(
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

    class _Adapter:
        result_cursor = "100:6"

        def __init__(self, src, *, client_factory=None):
            pass

        def poll(self, *, since=None):
            return [_signal(url="imap://src-1/100/6")]

        def fetch_bodies_and_attachments(self, uids):
            return {
                6: BodyAndAttachments(text="body", attachments=[_meta(0)], message_size_bytes=999)
            }

    seen: dict = {}
    monkeypatch.setattr("adapters.email_imap_source.EmailImapSource", _Adapter)
    monkeypatch.setattr("services.ingest.ingest_external_document", lambda **k: None)
    monkeypatch.setattr(
        fm,
        "process_signal",
        lambda raw, user_id=None: seen.update(
            {"attachments": list(raw.attachments), "body": raw.raw_content}
        ),
    )
    monkeypatch.setattr(fm, "_is_duplicate", lambda url, user_id: False)
    monkeypatch.setattr(fm, "_fetch_poll_cursor", lambda sid: "100:5")
    monkeypatch.setattr(fm, "_update_poll_timestamp", lambda s: None)
    monkeypatch.setattr(fm, "_persist_poll_cursor", lambda sid, cur: None)

    fm._poll_email_ingest_source(source)

    assert seen["body"] == "body"
    assert [a.filename for a in seen["attachments"]] == ["invoice.pdf"]


def test_attachment_write_failure_does_not_break_ingest(monkeypatch):
    """Ingest must not fail over metadata — the signal still lands."""
    from services import email_sender_rules

    store = _Store(raise_on_attachment=RuntimeError("db blip"))
    monkeypatch.setattr(config, "get_metadata_store", lambda: store)
    monkeypatch.setattr(email_sender_rules, "should_suppress", lambda *a: False)

    out = signal_processor.process_signal(_signal(attachments=[_meta(0)]), user_id="u1")

    assert out is not None
    assert any("INSERT INTO signals" in sql for sql, _ in store.executed)
