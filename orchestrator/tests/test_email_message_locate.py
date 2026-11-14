# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""One Message-ID lookup, shared by every call site (LUM-813).

Two implementations used to ship. `email_attachment_ingest`'s read
`signals.email_message_id` first; the copy in `routes/api_v1/email_attachments.py`
read `email_organize_log` **only**, and the attachment-download and send paths
imported that one — so mail moved outside Lumogis organize was declared gone
while sitting right there.

**The distinguishing assertion is "no organize-log row present."** With one, both
implementations return a Message-ID and every test passes either way. That is why
the divergence survived: it was invisible to the suite and only failed against a
real mailbox whose mail had moved.

Every consumer gets that assertion here, so re-introducing a log-only copy at any
one of them fails.
"""

from __future__ import annotations

import pytest

import config
from services import email_message_locate

MID = "<stored@example.com>"


class _Store:
    """Answers the two lookups, and records which ones were actually asked."""

    def __init__(self, *, signal_mid=None, organize_mid=None, folder=None, published_from=None):
        self.signal_mid = signal_mid
        self.organize_mid = organize_mid
        self.folder = folder
        self.published_from = published_from
        self.queried: list[str] = []

    def fetch_one(self, sql, params=None):
        if "FROM signals" in sql and "published_from" in sql and "email_message_id" not in sql:
            self.queried.append("published_from")
            if params and params[0] == "proj-1":
                return {"published_from": self.published_from} if self.published_from else None
            return None
        if "FROM signals" in sql and "email_message_id" in sql:
            self.queried.append("signals")
            sid = params[0] if params else None
            if sid == "orig-1":
                if self.signal_mid is None:
                    return None
                return {"email_message_id": self.signal_mid}
            if sid == "proj-1":
                return {"email_message_id": ""}
            return {"email_message_id": self.signal_mid} if self.signal_mid is not None else None
        if "FROM email_organize_log" in sql and "target" in sql:
            self.queried.append("organize_folder")
            return {"target": self.folder} if self.folder else None
        if "FROM email_organize_log" in sql:
            self.queried.append("organize_log")
            if not self.organize_mid:
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


# ---------------------------------------------------------------------------
# The shared implementation
# ---------------------------------------------------------------------------


def test_the_signals_column_wins_and_the_log_is_not_even_read(store):
    st = store(signal_mid=MID, organize_mid="<stale@example>")

    assert email_message_locate.message_id_for("sig-1", "alice") == MID
    assert st.queried == ["published_from", "signals"], (
        "the organize_log fallback must not be reached"
    )


def test_a_pre_069_signal_still_falls_back_to_the_organize_log(store):
    """Migration 069 added the column with no backfill — `''` on every old row."""
    st = store(signal_mid="", organize_mid=MID)

    assert email_message_locate.message_id_for("sig-1", "alice") == MID
    assert st.queried == ["published_from", "signals", "organize_log"]


def test_absent_from_both_returns_none_so_the_uid_is_used(store):
    store(signal_mid="", organize_mid=None)

    assert email_message_locate.message_id_for("sig-1", "alice") is None


def test_a_shared_projection_falls_back_to_the_originals_message_id(store):
    """Projection INSERT omits email_message_id; triage on the copy must still locate."""
    st = store(signal_mid=MID, published_from="orig-1")

    assert email_message_locate.message_id_for("proj-1", "alice") == MID
    assert st.queried == ["published_from", "signals", "signals"]


def test_the_lookup_is_scoped_to_the_caller(store, monkeypatch):
    seen: list = []

    class _Scoped(_Store):
        def fetch_one(self, sql, params=None):
            if "FROM signals" in sql:
                seen.append(params)
            return super().fetch_one(sql, params)

    monkeypatch.setattr(config, "get_metadata_store", lambda: _Scoped(signal_mid=MID))

    email_message_locate.message_id_for("sig-1", "mallory")

    assert seen[0] == ("sig-1", "mallory")


# ---------------------------------------------------------------------------
# Every call site resolves it WITHOUT an organize-log row
# ---------------------------------------------------------------------------


def test_the_ingest_sweep_uses_the_complete_lookup(store):
    from services import email_attachment_ingest

    st = store(signal_mid=MID, organize_mid=None)

    assert email_attachment_ingest._message_id_for("sig-1", "alice") == MID
    assert "signals" in st.queried


def test_the_attachment_download_route_uses_the_complete_lookup(store):
    """This is the call site that had the incomplete copy inlined beside it."""
    from routes.api_v1 import email_attachments

    st = store(signal_mid=MID, organize_mid=None)

    assert email_attachments.message_id_for("sig-1", "alice") == MID
    assert "signals" in st.queried


def test_the_body_read_route_uses_the_complete_lookup(store):
    from routes.api_v1 import email_send

    st = store(signal_mid=MID, organize_mid=None)

    assert email_send._message_id_for("sig-1", "alice") == MID
    assert "signals" in st.queried


def test_the_send_handler_uses_the_complete_lookup(store):
    """`smtp_send` imported the route-local pair, so it inherited the hole."""
    from actions.handlers import smtp_send

    st = store(signal_mid=MID, organize_mid=None)

    assert smtp_send._original_message_id("sig-1", "alice") == MID
    assert "signals" in st.queried


def test_no_call_site_defines_its_own_copy():
    """The trap was two implementations that looked interchangeable.

    Deleting the duplicates is the fix; this fails if one grows back.
    """
    import inspect

    from actions.handlers import smtp_send
    from routes.api_v1 import email_attachments
    from routes.api_v1 import email_send

    from services import email_attachment_ingest

    canonical = email_message_locate.message_id_for
    for mod, attr in [
        (email_attachment_ingest, "_message_id_for"),
        (email_attachments, "message_id_for"),
        (email_send, "_message_id_for"),
    ]:
        assert getattr(mod, attr) is canonical, f"{mod.__name__}.{attr} is not the shared function"

    # smtp_send imports lazily inside the wrapper, so assert on the source.
    src = inspect.getsource(smtp_send._original_message_id)
    assert "email_message_locate" in src
    assert "email_attachments" not in src


# ---------------------------------------------------------------------------
# The folder hint — identical in both former copies, shared for the same reason
# ---------------------------------------------------------------------------


def test_the_folder_hint_answers_only_for_lumogis_moves(store):
    store(folder="Lumogis/Receipts")

    assert email_message_locate.organize_folder_for("sig-1", "alice") == "Lumogis/Receipts"


def test_the_folder_hint_is_absent_when_lumogis_did_not_move_it(store):
    store(folder=None)

    assert email_message_locate.organize_folder_for("sig-1", "alice") is None
