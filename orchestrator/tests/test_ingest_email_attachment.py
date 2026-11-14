# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""``ingest_email_attachment`` — the library write for an auto-filed attachment (LUM-702).

The load-bearing test here is
:func:`test_ingest_email_attachment_never_writes_sources_poll_cursor`, and it is
worth saying why a structural assertion earns its place.

``ingest_external_document`` — the sibling twenty lines up in the same file —
writes ``sources.poll_cursor`` in three places. For an IMAP source that column is
the mailbox's UID watermark (``"100:5"``), and the guard protecting those writes
is a *string* comparison: ``'100:5' < '2026-07-25T10:00:00Z'`` is ``True``. So the
guard permits the overwrite rather than preventing it. A mailbox whose watermark
became a timestamp would either fail to parse on the next poll or re-ingest the
entire mailbox — and the symptom would appear in ``feed_monitor``, hours later, in
a component this feature never touches.

That is why this is a separate function rather than a flag, and why the guard is
a test rather than a comment. It has been mutation-checked: adding the UPDATE by
hand makes it fail.
"""

from __future__ import annotations

import pytest
from services.ingest import ingest_email_attachment
from services.point_ids import email_attachment_chunk_point_id

from services import ingest as ingest_mod

USER = "alice"
SOURCE = "11111111-1111-1111-1111-111111111111"
SIGNAL = "email:src-a:42"
PART = 2


class _RecordingStore:
    """Records every statement so a test can assert what was NOT executed."""

    def __init__(self) -> None:
        self.queries: list[str] = []

    def execute(self, query, params=None):
        self.queries.append(query)

    def fetch_one(self, query, params=None):
        self.queries.append(query)
        return None

    def fetch_all(self, query, params=None):
        self.queries.append(query)
        return []

    class _Txn:
        def __enter__(self):
            return None

        def __exit__(self, *a):
            return False

    def transaction(self):
        return self._Txn()


@pytest.fixture
def store(monkeypatch):
    st = _RecordingStore()
    import config

    monkeypatch.setitem(config._instances, "metadata_store", st)
    return st


@pytest.fixture(autouse=True)
def _no_entity_extraction(monkeypatch):
    """Entity extraction is a different subsystem with its own tests."""
    monkeypatch.setattr(ingest_mod, "_emit_document_ingested_and_entities", lambda **kw: [])


def _ingest(**over):
    kwargs = dict(
        user_id=USER,
        source_id=SOURCE,
        signal_id=SIGNAL,
        part_index=PART,
        filename="invoice.pdf",
        content="Invoice number 4471. Amount due 240.00 EUR. Due 2026-08-01.",
    )
    kwargs.update(over)
    return ingest_email_attachment(**kwargs)


# --- 1 ----------------------------------------------------------------------


def test_files_the_text_under_an_email_attachment_logical_path(store):
    result = _ingest()

    assert result.file_path == f"email-attachment://{SOURCE}/{SIGNAL}/{PART}"
    assert result.chunk_count > 0
    assert result.skipped is False


# --- 2 ----------------------------------------------------------------------


def test_re_ingesting_the_same_part_replaces_rather_than_duplicates_vectors(store, monkeypatch):
    """The delete must happen BEFORE the write, or a shrinking document leaves
    orphaned chunks that stay searchable."""
    calls: list[str] = []

    monkeypatch.setattr(
        ingest_mod,
        "_delete_document_vectors_for_path",
        lambda **kw: calls.append(f"delete:{kw['file_path']}"),
    )
    real_write = ingest_mod._ingest_chunked_text

    def _spy(**kw):
        calls.append("write")
        return real_write(**kw)

    monkeypatch.setattr(ingest_mod, "_ingest_chunked_text", _spy)

    _ingest()

    assert calls[0] == f"delete:email-attachment://{SOURCE}/{SIGNAL}/{PART}"
    assert "write" in calls
    assert calls.index("write") > 0


def test_the_prior_chunk_count_is_passed_as_the_delete_fallback(store, monkeypatch):
    """Legacy deterministic ids are cleared by count; a wrong fallback strands them."""
    seen: dict = {}
    monkeypatch.setattr(
        ingest_mod, "_delete_document_vectors_for_path", lambda **kw: seen.update(kw)
    )

    _ingest(prior_chunk_count=7)

    assert seen["chunk_count_fallback"] == 7


def test_a_first_filing_uses_a_zero_fallback(store, monkeypatch):
    seen: dict = {}
    monkeypatch.setattr(
        ingest_mod, "_delete_document_vectors_for_path", lambda **kw: seen.update(kw)
    )

    _ingest()

    assert seen["chunk_count_fallback"] == 0


# --- 3 ----------------------------------------------------------------------


def test_empty_text_writes_no_chunks_and_reports_it(store):
    result = _ingest(content="   \n\t  ")

    assert result.chunk_count == 0
    assert result.skipped is True


# --- 4 ----------------------------------------------------------------------


def test_point_ids_are_deterministic_for_the_same_part():
    first = email_attachment_chunk_point_id(USER, SIGNAL, PART, 0)
    again = email_attachment_chunk_point_id(USER, SIGNAL, PART, 0)

    assert first == again, "a re-ingest must overwrite, not accumulate"


@pytest.mark.parametrize(
    "a,b",
    [
        ((USER, SIGNAL, PART, 0), (USER, SIGNAL, PART, 1)),  # chunk
        ((USER, SIGNAL, PART, 0), (USER, SIGNAL, PART + 1, 0)),  # part
        ((USER, SIGNAL, PART, 0), (USER, "email:src-a:43", PART, 0)),  # message
        ((USER, SIGNAL, PART, 0), ("bob", SIGNAL, PART, 0)),  # user
    ],
)
def test_point_ids_differ_on_every_component(a, b):
    assert email_attachment_chunk_point_id(*a) != email_attachment_chunk_point_id(*b)


# --- 9 — the structural guard -----------------------------------------------


def test_ingest_email_attachment_never_writes_sources_poll_cursor(store):
    """For an IMAP source ``poll_cursor`` is the UID watermark, and the paperless
    sibling's guard is a string comparison that PERMITS overwriting it."""
    _ingest()

    offenders = [q for q in store.queries if "poll_cursor" in q.lower()]
    assert offenders == [], (
        "ingest_email_attachment must never touch sources.poll_cursor — for an "
        f"IMAP source that is the mailbox UID watermark. Executed: {offenders}"
    )


def test_ingest_email_attachment_has_no_poll_watermark_parameter():
    """A future caller must not be able to opt into cursor advancement by
    widening this function — that is how the flag-on-a-shared-function outage
    happens."""
    import inspect

    params = inspect.signature(ingest_email_attachment).parameters

    assert "poll_watermark" not in params
    assert "stored_source_poll_cursor" not in params


def test_the_result_never_asks_the_caller_to_advance_the_cursor(store):
    result = _ingest()

    assert result.advance_external_poll_cursor is False


def test_the_paperless_sibling_still_does_write_the_cursor():
    """Anchors the contrast this whole guard exists for.

    If this ever fails, the sibling changed and the comment above — and the
    reason for a separate function — needs re-reading rather than deleting.
    """
    import inspect

    src = inspect.getsource(ingest_mod.ingest_external_document)

    assert src.count("poll_cursor = %s") == 3
