# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Persisting the Message-ID so an attachment refetch survives ANY move.

Lumogis stores no attachment bytes (LUM-695) — opening one refetches the part by
IMAP uid. A uid is valid only within one folder, so a move breaks the refetch and
the sweep records ``message_gone``, which is **terminal** under the full
``uq_..._part`` index (067). The attachment is never offered again.

A Message-ID fallback existed for exactly this, but it read from
``email_organize_log`` — a row that only exists when **Lumogis's own** organize
moved the message. Every other mover produced the terminal skip: the user in
their mail client, a server-side filter, and above all a paperless-ngx mail rule,
which moves or deletes on success by default. A user running both systems lost
attachments systematically, and silently.

Migration 069 stores the header on the signal at ingest, so the fallback is
available whoever did the moving.
"""

from __future__ import annotations

import pytest

import config
from services import email_attachment_ingest
from services import signal_processor

MID = "<inv-2026-07@utility.example>"


# --- extraction --------------------------------------------------------------


@pytest.mark.parametrize("key", ["Message-ID", "Message-Id", "message-id", "MESSAGE-ID"])
def test_the_header_is_found_whatever_its_capitalisation(key):
    """Servers differ on the spelling. Matching one form would silently store
    nothing for half of them — and "silently stores nothing" is precisely the
    failure this column exists to remove."""
    assert signal_processor._extract_message_id({key: MID}) == MID


def test_the_value_is_trimmed():
    assert signal_processor._extract_message_id({"Message-ID": f"  {MID}  "}) == MID


@pytest.mark.parametrize("headers", [None, {}, {"From": "a@b.example"}, {"Message-ID": ""}])
def test_a_message_without_the_header_yields_empty_not_none(headers):
    """``''`` keeps the column NOT NULL and reads as "fall back to the uid",
    which is the pre-069 behaviour."""
    assert signal_processor._extract_message_id(headers) == ""


# --- the read path — where the fix actually lands ----------------------------


class _Store:
    """Answers the two lookups ``_message_id_for`` makes, and records which
    ones it was actually asked."""

    def __init__(self, *, signal_mid=None, organize_mid=None):
        self.signal_mid = signal_mid
        self.organize_mid = organize_mid
        self.queried: list[str] = []

    def fetch_one(self, sql, params=None):
        if "published_from" in sql:
            # LUM-661 taught `_lookup_ids` to resolve a shared projection back to
            # its personal original before looking up the Message-ID. This fake
            # raises on any query it does not recognise, so it must know about
            # that lookup or every caller of `message_id_for` fails here.
            # None = "not a projection", which is the case these tests exercise.
            return None
        if "FROM signals" in sql and "email_message_id" in sql:
            self.queried.append("signals")
            return {"email_message_id": self.signal_mid} if self.signal_mid is not None else None
        if "FROM email_organize_log" in sql:
            self.queried.append("organize_log")
            if self.organize_mid is None:
                return None
            return {"reverse_token": {"message_id": self.organize_mid}}
        raise AssertionError(f"unexpected query: {sql!r}")


@pytest.fixture
def store(monkeypatch):
    def _install(**kw):
        st = _Store(**kw)
        monkeypatch.setattr(config, "get_metadata_store", lambda: st)
        return st

    return _install


def test_the_signals_column_is_used_when_present(store):
    store(signal_mid=MID)

    assert email_attachment_ingest._message_id_for("sig-1", "alice") == MID


def test_the_signals_column_wins_and_the_log_is_not_even_read(store):
    """Order matters for cost as well as correctness: the stored column is
    authoritative and always present post-069, so the log lookup — a correlated
    subquery over `signals` — should not run at all."""
    st = store(signal_mid=MID, organize_mid="<stale@example>")

    assert email_attachment_ingest._message_id_for("sig-1", "alice") == MID
    assert st.queried == ["signals"], "the organize_log fallback must not be reached"


def test_a_pre_069_signal_still_falls_back_to_the_organize_log(store):
    """Rows ingested before the migration carry ``''``. They must keep the
    behaviour they had, not lose it — a backfill-free migration is only safe if
    the old path survives."""
    st = store(signal_mid="", organize_mid=MID)

    assert email_attachment_ingest._message_id_for("sig-1", "alice") == MID
    assert st.queried == ["signals", "organize_log"]


def test_no_message_id_anywhere_returns_none_so_the_uid_is_used(store):
    store(signal_mid="", organize_mid=None)

    assert email_attachment_ingest._message_id_for("sig-1", "alice") is None


def test_a_missing_signal_row_does_not_crash_the_refetch(store):
    """Signals are retained when a source is deleted (LUM-701) but a purge can
    still remove one. The attachment path must degrade, not raise."""
    store(signal_mid=None, organize_mid=None)

    assert email_attachment_ingest._message_id_for("sig-1", "alice") is None


def test_the_lookup_is_scoped_to_the_caller(store, monkeypatch):
    seen: list = []

    class _Scoped(_Store):
        def fetch_one(self, sql, params=None):
            if "FROM signals" in sql:
                seen.append(params)
            return super().fetch_one(sql, params)

    st = _Scoped(signal_mid=MID)
    monkeypatch.setattr(config, "get_metadata_store", lambda: st)

    email_attachment_ingest._message_id_for("sig-1", "mallory")

    assert seen[0] == ("sig-1", "mallory")


# --- the write path ----------------------------------------------------------


class _Capture:
    """Captures the resolved INSERT rather than parsing the source text."""

    def __init__(self):
        self.sql = ""
        self.params: tuple = ()

    def execute(self, sql, params=None):
        if "INSERT INTO signals" in sql:
            self.sql, self.params = sql, params or ()

    def fetch_one(self, sql, params=None):
        return None

    def fetch_all(self, sql, params=None):
        return []


def _persisted(monkeypatch, **signal_kw):
    from models.signals import Signal

    cap = _Capture()
    monkeypatch.setattr(config, "get_metadata_store", lambda: cap)
    # `_persist` also embeds for semantic dedup; that arm is not under test.
    monkeypatch.setattr(config, "get_vector_store", lambda: None, raising=False)

    base = dict(
        signal_id="11111111-1111-1111-1111-111111111111",
        user_id="alice",
        source_id="22222222-2222-2222-2222-222222222222",
        title="Your July invoice",
        url="imap://mailbox/INBOX/17",
        published_at=None,
        content_summary="",
        raw_content="",
        entities=[],
        topics=[],
        importance_score=0.0,
        relevance_score=0.0,
        notified=False,
        created_at=None,
    )
    base.update(signal_kw)
    try:
        signal_processor._persist(Signal(**base))
    except Exception:  # noqa: BLE001 - the embed arm may fail; the INSERT is captured
        pass
    assert cap.sql, "the INSERT was never issued — the fixture, not the code, is wrong"
    return cap


def _columns(sql: str) -> list[str]:
    inner = sql.split("INSERT INTO signals", 1)[1].split("(", 1)[1].split(")", 1)[0]
    return [c.strip() for c in inner.split(",")]


def test_the_insert_lists_the_new_column(monkeypatch):
    cap = _persisted(monkeypatch, email_message_id=MID)

    assert "email_message_id" in _columns(cap.sql)


def test_every_column_has_a_value_slot(monkeypatch):
    """The signals INSERT is a long POSITIONAL statement. A column added without
    its ``%s`` shifts every parameter after it — the row is written with values
    in the wrong columns and nothing raises."""
    cap = _persisted(monkeypatch, email_message_id=MID)

    n_columns = len(_columns(cap.sql))
    # Two columns are filled by correlated sub-SELECTs, not by placeholders —
    # but those sub-SELECTs take a %s of their own, which is already in params.
    values = cap.sql.split("VALUES", 1)[1]
    n_slots = values.count("%s") - 2 + 2

    assert n_columns == n_slots, f"{n_columns} columns vs {n_slots} value slots"


def test_the_message_id_lands_in_its_own_column(monkeypatch):
    """Position, not just presence. This is the assertion that catches an
    off-by-one that leaves the statement well-formed but the data wrong."""
    cap = _persisted(monkeypatch, email_message_id=MID, from_addr="billing@utility.example")

    idx = _columns(cap.sql).index("email_message_id")

    assert cap.params[idx] == MID


def test_a_non_email_signal_stores_an_empty_string_not_null(monkeypatch):
    """The column is NOT NULL DEFAULT ''. Binding None would fail the insert for
    every RSS signal in the system."""
    cap = _persisted(monkeypatch)

    idx = _columns(cap.sql).index("email_message_id")

    assert cap.params[idx] == ""
