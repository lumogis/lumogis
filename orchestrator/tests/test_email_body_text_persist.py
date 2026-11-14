# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Message-text persistence through the ingest pipeline (LUM-819).

`extract_text` already runs at ingest and its output is discarded. This is the
write that stops discarding it, and every test here guards a defect the plan
review found by reading the code rather than by running it:

* **Source of the text.** `process_signal` constructs the outgoing `Signal`
  with ``raw_content=""  # cleared -- transient only``. A write conditioned on
  the *persisted* signal's ``raw_content`` therefore reads an empty string every
  single time and stores nothing, silently. The text is on the RAW signal.

* **The guard.** The neighbouring `_persist_attachments` call site is
  ``if is_email and raw_signal.attachments:`` -- a compound condition. Reusing
  it would gate message text on the message having an ATTACHMENT, which breaks
  search for the majority of plain mail while looking correct.

* **Conflict handling.** Same rule as attachments: a plain INSERT raises on any
  reprocessed signal, and `feed_monitor`'s poll loop ``break``s on a handler
  error, so one duplicate stalls the whole tick rather than one message.

* **Ordering.** Written AFTER `_persist`, like attachment rows, so a suppressed
  sender's message -- which never gets a `signals` row -- cannot leave a body
  row behind (here it would fail the FK, logging on every silenced message).
"""

from __future__ import annotations

from datetime import datetime
from datetime import timezone

import pytest
from models.signals import Signal

import config
from services import signal_processor


class _Store:
    """Records every execute() so the tests can assert on the SQL that ran."""

    def __init__(self, *, raise_on_body: Exception | None = None) -> None:
        self.executed: list[tuple[str, tuple]] = []
        self._raise = raise_on_body

    def execute(self, sql, params=None):
        if self._raise is not None and "email_body_text" in sql:
            raise self._raise
        self.executed.append((sql, params or ()))
        return None

    def fetch_one(self, sql, params=None):
        return None

    def fetch_all(self, sql, params=None):
        return []

    def body_writes(self) -> list[tuple]:
        return [p for sql, p in self.executed if "INSERT INTO email_body_text" in sql]

    def body_sql(self) -> list[str]:
        return [sql for sql, _ in self.executed if "INSERT INTO email_body_text" in sql]

    def signal_writes(self) -> list[tuple]:
        return [p for sql, p in self.executed if "INSERT INTO signals" in sql]


def _signal(**kw) -> Signal:
    base = dict(
        signal_id="",
        source_id="src-1",
        title="Invoice",
        url="imap://src-1/100/42",
        published_at=None,
        content_summary="",
        raw_content="Please find attached our invoices for the plumbing work.",
        entities=[],
        topics=[],
        importance_score=0.0,
        relevance_score=0.0,
        notified=False,
        created_at=datetime(2026, 8, 10, tzinfo=timezone.utc),
        user_id="u1",
        source_type="imap",
        category="",
        headers={"From": "office@plumbers.example", "Subject": "Invoice"},
    )
    base.update(kw)
    return Signal(**base)


@pytest.fixture
def store(monkeypatch):
    s = _Store()
    monkeypatch.setattr(config, "get_metadata_store", lambda: s)
    return s


@pytest.fixture(autouse=True)
def _quiet_pipeline(monkeypatch):
    """Neutralise everything process_signal does that is not under test."""
    from services import email_sender_rules
    from services import email_unsubscribe

    monkeypatch.setattr(email_unsubscribe, "record_from_headers", lambda **kw: None)
    monkeypatch.setattr(email_sender_rules, "should_suppress", lambda *a: False)
    monkeypatch.setattr(signal_processor, "_notify", lambda sig: False)
    monkeypatch.setattr(signal_processor, "_load_profile", lambda uid: None)
    monkeypatch.setattr(signal_processor.hooks, "fire_background", lambda *a, **k: None)


# ---------------------------------------------------------------------------
# Where the text comes from
# ---------------------------------------------------------------------------


def test_body_text_is_read_from_the_raw_signal_not_the_persisted_one(store):
    """The defect the plan would have shipped.

    ``process_signal`` blanks ``raw_content`` on the Signal it builds, so a
    write reading the persisted signal stores nothing for every message ever
    ingested -- with no error and no log line.
    """
    signal_processor.process_signal(_signal(), user_id="u1")

    writes = store.body_writes()
    assert len(writes) == 1, "expected exactly one email_body_text row"
    assert writes[0][2] == "Please find attached our invoices for the plumbing work."


def test_body_text_is_stored_for_mail_without_any_attachment(store):
    """The compound-guard defect.

    The neighbouring attachment write is gated on ``raw_signal.attachments``.
    Reusing that guard would store message text only for mail that happens to
    carry an attachment -- most plain mail would silently never be searchable.
    """
    signal_processor.process_signal(_signal(attachments=[]), user_id="u1")

    assert len(store.body_writes()) == 1


def test_metadata_mode_mail_stores_no_body_text(store):
    """Metadata mode fetches no body, so ``raw_content`` is empty by construction."""
    signal_processor.process_signal(_signal(raw_content=""), user_id="u1")

    assert store.body_writes() == []


def test_non_email_signals_never_store_body_text(store):
    """An RSS article's text is not mail and must not enter the mail corpus."""
    signal_processor.process_signal(
        _signal(source_type="rss", url="https://example.com/x"), user_id="u1"
    )

    assert store.body_writes() == []


def test_suppressed_sender_mail_stores_no_body_text(store, monkeypatch):
    """A silenced sender gets no ``signals`` row, so it must get no body row.

    Written after ``_persist`` for the same reason attachment rows are: the
    suppression path returns before it.
    """
    from services import email_sender_rules

    monkeypatch.setattr(email_sender_rules, "should_suppress", lambda *a: True)

    signal_processor.process_signal(_signal(), user_id="u1")

    assert store.body_writes() == []
    assert store.signal_writes() == []


# ---------------------------------------------------------------------------
# Robustness -- ingest must never fail over a search index
# ---------------------------------------------------------------------------


def test_reprocessing_a_signal_does_not_raise(store):
    """``feed_monitor`` breaks its poll loop on a handler error."""
    signal_processor.process_signal(_signal(), user_id="u1")

    sql = store.body_sql()
    assert sql, "expected a body-text insert"
    assert "ON CONFLICT" in sql[0].upper()
    assert "DO NOTHING" in sql[0].upper()


def test_ingest_survives_a_body_text_write_failure(monkeypatch):
    """The signal still persists when the search-index write blows up."""
    s = _Store(raise_on_body=RuntimeError("postgres is having a day"))
    monkeypatch.setattr(config, "get_metadata_store", lambda: s)

    signal_processor.process_signal(_signal(), user_id="u1")

    assert s.signal_writes(), "the signals row must still have been written"
    assert s.body_writes() == []


def test_body_text_over_the_cap_is_truncated_and_flagged(store, monkeypatch):
    monkeypatch.setattr(signal_processor, "_MAX_BODY_TEXT_BYTES", 32)

    signal_processor.process_signal(_signal(raw_content="x" * 100), user_id="u1")

    writes = store.body_writes()
    assert len(writes) == 1
    text, truncated = writes[0][2], writes[0][3]
    assert len(text.encode("utf-8")) <= 32
    assert truncated is True


def test_body_text_within_the_cap_is_not_flagged(store):
    signal_processor.process_signal(_signal(), user_id="u1")

    assert store.body_writes()[0][3] is False


def test_a_multibyte_body_is_never_cut_mid_character(store, monkeypatch):
    """Truncation is by BYTES, so a naive slice can split a UTF-8 sequence."""
    monkeypatch.setattr(signal_processor, "_MAX_BODY_TEXT_BYTES", 9)

    signal_processor.process_signal(_signal(raw_content="Müller Sanitär"), user_id="u1")

    text = store.body_writes()[0][2]
    assert isinstance(text, str)
    assert len(text.encode("utf-8")) <= 9
    assert text == text.encode("utf-8").decode("utf-8"), "no partial character"
