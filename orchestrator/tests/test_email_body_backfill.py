# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Backfill of stored message text (LUM-819).

Every test here guards a defect the plan review found rather than a happy path:

* **The privacy control.** Nothing originally restricted the backfill to
  full-ingest sources, so it would have downloaded and stored body text for
  mailboxes whose owner explicitly opted out of body reading — retroactively,
  across their whole mail history. The test asserts **no adapter is
  constructed**, not merely that no row was written: a fetch that discards its
  result has already read the mail.

* **Termination.** Without a dead-candidate marker the candidate query
  re-selects its own failures every run; once they outnumber ``--limit`` the job
  scans dead rows forever while reporting healthy counts.

* **Projections.** ``project_signal`` copies ``url`` verbatim onto the shared
  row, so without ``scope = 'personal'`` every shared message is fetched twice.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from services import email_body_backfill


@dataclass
class _Fetched:
    text: str
    reason: str = "ok"


class _Adapter:
    constructed: list = []

    def __init__(self, source):
        _Adapter.constructed.append(source)
        self.calls: list[dict] = []

    def fetch_body_bounded(self, *, uid, message_id, uidvalidity=0, folder_hint=None):
        self.calls.append({"uid": uid, "message_id": message_id})
        return _Fetched(text=f"body for uid {uid}")


class _Store:
    def __init__(self, candidates, source_row):
        self.candidates = candidates
        self.source_row = source_row
        self.executed: list[tuple[str, tuple]] = []
        self.queries: list[str] = []

    def fetch_all(self, sql, params=None):
        self.queries.append(sql)
        return list(self.candidates)

    def fetch_one(self, sql, params=None):
        return dict(self.source_row) if self.source_row else None

    def execute(self, sql, params=None):
        self.executed.append((sql, tuple(params or ())))

    def body_writes(self):
        return [p for s, p in self.executed if "INSERT INTO email_body_text " in s]

    def dead_writes(self):
        return [p for s, p in self.executed if "email_body_text_dead" in s]


def _candidate(n=1):
    return {
        "signal_id": f"sig-{n}",
        "url": f"imap://src-1/100/{n}",
        "source_id": "src-1",
        "user_id": "u1",
        "email_message_id": f"<m{n}@x>",
    }


def _source(extraction_method="imap_full_ingest"):
    return {
        "id": "src-1",
        "name": "Mailbox",
        "source_type": "imap",
        "url": "imap://host/INBOX",
        "category": "",
        "active": True,
        "poll_interval": 300,
        "extraction_method": extraction_method,
        "css_selector_override": None,
        "last_polled_at": None,
        "last_signal_at": None,
        "poll_cursor": None,
        "user_id": "u1",
    }


@pytest.fixture(autouse=True)
def _wire(monkeypatch):
    _Adapter.constructed = []
    monkeypatch.setattr("adapters.email_imap_source.EmailImapSource", _Adapter)
    monkeypatch.setattr("signals.feed_monitor._row_to_source", lambda row: row)
    monkeypatch.setenv("EMAIL_BODY_BACKFILL_PACE_SECONDS", "0")


def _run(store):
    import config as cfg

    orig = cfg.get_metadata_store
    cfg.get_metadata_store = lambda: store
    try:
        return email_body_backfill.backfill_email_bodies(limit=10)
    finally:
        cfg.get_metadata_store = orig


# ---------------------------------------------------------------------------
# The privacy control
# ---------------------------------------------------------------------------


def test_backfill_never_touches_a_metadata_mode_source():
    """Mutation check: drop the extraction_method guard and this fails.

    Asserting on the adapter, not the row write: a fetch whose result is
    discarded has still read the user's mail off the server.
    """
    store = _Store([_candidate()], _source(extraction_method="imap_metadata"))

    result = _run(store)

    assert _Adapter.constructed == [], "no IMAP connection may be opened at all"
    assert store.body_writes() == []
    assert result["skipped"] == 1


def test_backfill_does_run_for_a_full_ingest_source():
    store = _Store([_candidate()], _source())

    result = _run(store)

    assert result["stored"] == 1
    assert store.body_writes()[0][2] == "body for uid 1"


# ---------------------------------------------------------------------------
# Termination and scope
# ---------------------------------------------------------------------------


def test_candidates_exclude_rows_that_already_have_body_or_dead_markers():
    """Mutation check: drop either NOT EXISTS and this fails."""
    store = _Store([_candidate()], _source())

    _run(store)

    sql = store.queries[0]
    assert "FROM email_body_text b" in sql
    assert "FROM email_body_text_dead d" in sql


def test_candidates_exclude_shared_projection_rows():
    """``project_signal`` copies ``url`` verbatim, so scope must be pinned."""
    store = _Store([_candidate()], _source())

    _run(store)

    assert "s.scope = 'personal'" in store.queries[0]


def test_candidates_are_email_only_and_newest_first():
    store = _Store([_candidate()], _source())

    _run(store)

    sql = store.queries[0]
    # `%%` — psycopg2 %-formats the query before Postgres sees it.
    assert "s.url LIKE 'imap://%%'" in sql
    assert "ORDER BY s.created_at DESC" in sql


def test_a_terminal_fetch_failure_is_marked_dead(monkeypatch):
    class _Gone(_Adapter):
        def fetch_body_bounded(self, **kw):
            return _Fetched(text="", reason="not_found")

    monkeypatch.setattr("adapters.email_imap_source.EmailImapSource", _Gone)
    store = _Store([_candidate()], _source())

    result = _run(store)

    assert result["dead"] == 1
    assert store.dead_writes()[0][1] == "not_found"
    assert store.body_writes() == []


def test_a_transient_fetch_failure_is_not_marked_dead(monkeypatch):
    class _Flaky(_Adapter):
        def fetch_body_bounded(self, **kw):
            raise TimeoutError("mailbox is slow")

    monkeypatch.setattr("adapters.email_imap_source.EmailImapSource", _Flaky)
    store = _Store([_candidate()], _source())

    result = _run(store)

    assert result["skipped"] == 1
    # It records ONE attempt rather than nothing: a message that always times
    # out must not be re-selected forever, but one timeout must not condemn it.
    assert store.dead_writes()[0][2] == 1, "a timeout stays retryable"


def test_a_terminal_failure_lands_at_the_attempt_ceiling_immediately():
    """Retrying a message the server says is gone is pointless."""

    class _Gone(_Adapter):
        def fetch_body_bounded(self, **kw):
            return _Fetched(text="", reason="not_found")

    import services.email_body_backfill as mod

    store = _Store([_candidate()], _source())
    import adapters.email_imap_source as adapter_mod

    prev, adapter_mod.EmailImapSource = adapter_mod.EmailImapSource, _Gone
    try:
        _run(store)
    finally:
        adapter_mod.EmailImapSource = prev

    assert store.dead_writes()[0][2] == mod._MAX_ATTEMPTS


def test_dead_candidates_are_excluded_only_at_the_ceiling():
    """A single failure must not permanently remove a message from the queue."""
    store = _Store([_candidate()], _source())

    _run(store)

    assert "d.attempts >= %s" in store.queries[0]


def test_a_temporarily_unreachable_mailbox_is_not_retired(monkeypatch):
    """The outage regression.

    Every failing ``FetchedBody`` carries ``text=""``, so classifying on
    ``reason in _TERMINAL_REASONS or not text`` made the transient branch
    unreachable — one IMAP outage during a run permanently retired up to
    ``--limit`` messages that were merely temporarily unreachable.

    Mutation check: add ``or not text`` back to the terminal test and this fails.
    """

    class _Down(_Adapter):
        def fetch_body_bounded(self, **kw):
            return _Fetched(text="", reason="mailbox_unreachable")

    monkeypatch.setattr("adapters.email_imap_source.EmailImapSource", _Down)
    store = _Store([_candidate()], _source())

    result = _run(store)

    assert result["skipped"] == 1
    assert result["dead"] == 0, "an outage must not retire the message"
    assert store.dead_writes()[0][2] == 1, "one attempt recorded, not the ceiling"


@pytest.mark.parametrize("reason", ["not_found", "too_large", "ambiguous", "no_body"])
def test_the_terminal_reasons_are_the_adapter_s_actual_vocabulary(monkeypatch, reason):
    """The set previously listed two reasons the adapter never emits and missed
    two it does, which made it inert."""

    class _Gone(_Adapter):
        def fetch_body_bounded(self, **kw):
            return _Fetched(text="", reason=reason)

    monkeypatch.setattr("adapters.email_imap_source.EmailImapSource", _Gone)
    store = _Store([_candidate()], _source())

    assert _run(store)["dead"] == 1, f"{reason} should be terminal"


def test_an_unreachable_source_skips_its_whole_group(monkeypatch):
    class _Dead(_Adapter):
        def __init__(self, source):
            raise OSError("connection refused")

    monkeypatch.setattr("adapters.email_imap_source.EmailImapSource", _Dead)
    store = _Store([_candidate(1), _candidate(2)], _source())

    result = _run(store)

    assert result["skipped"] == 2
    assert result["stored"] == 0


def test_body_writes_are_conflict_safe():
    """A re-run must not raise on a row another run already stored."""
    store = _Store([_candidate()], _source())

    _run(store)

    sql = next(s for s, _ in store.executed if "INSERT INTO email_body_text " in s)
    assert "ON CONFLICT" in sql.upper() and "DO NOTHING" in sql.upper()


def test_no_candidates_is_a_clean_no_op():
    store = _Store([], _source())

    result = _run(store)

    assert result == {"scanned": 0, "stored": 0, "skipped": 0, "dead": 0, "sources": 0}
    assert _Adapter.constructed == []
