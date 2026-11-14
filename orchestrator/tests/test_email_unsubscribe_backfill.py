# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""LUM-711 — populate the Subscriptions list from mail ingested before LUM-697.

Hermetic, and shaped after `test_email_sender_backfill.py` for the same reason
the module is shaped after its subject: a fake metadata store answers the
candidate/source/known-sender queries, and a fake adapter returns canned
headers. Proves the happy path, every safe-skip branch, and the two properties
that are specific to *this* backfill rather than inherited from the template:

* **it reuses the live upsert** rather than a second copy of the
  preserve-on-empty rules, and
* **a re-run does not inflate `message_count`**, because the live upsert
  increments on every call and replaying the archive would count without bound.
"""

from __future__ import annotations

import pytest

import config
from services import email_unsubscribe_backfill as bf


class FakeStore:
    def __init__(self):
        self.signals: list[dict] = []
        self.sources: dict[str, dict] = {}
        self.known: list[dict] = []

    def add_candidate(self, source_id, uidvalidity, uid, *, from_addr, user_id="u1"):
        self.signals.append(
            {
                "signal_id": f"sig{len(self.signals)}",
                "source_id": source_id,
                "url": f"imap://{source_id}/{uidvalidity}/{uid}",
                "user_id": user_id,
                "from_addr": from_addr,
                "from_display": "Sender Name",
            }
        )

    def add_known_sender(self, source_id, from_addr, user_id="u1"):
        self.known.append({"user_id": user_id, "source_id": source_id, "from_addr": from_addr})

    def add_source(self, source_id, source_type="imap"):
        self.sources[source_id] = {
            "id": source_id,
            "name": "mbox",
            "source_type": source_type,
            "url": "imap://h",
            "category": "email",
            "active": True,
            "poll_interval": 900,
            "extraction_method": "imap_metadata",
            "css_selector_override": None,
            "last_polled_at": None,
            "last_signal_at": None,
            "poll_cursor": None,
            "user_id": "u1",
        }

    def ping(self):
        return True

    def close(self):
        pass

    def fetch_all(self, query, params=None):
        q = query.lower()
        if "from email_unsubscribe_target" in q:
            return list(self.known)
        if "from signals" in q:
            return list(self.signals)
        return []

    def fetch_one(self, query, params=None):
        if "from sources" in query.lower():
            return self.sources.get((params or ("",))[0])
        return None

    def execute(self, query, params=None):
        pass


class FakeAdapter:
    current_uidvalidity = 100
    headers_by_uid: dict[int, dict] = {}
    raise_on_connect = False

    def __init__(self, source, *, client_factory=None):
        self.source = source

    def fetch_headers(self, uids):
        if FakeAdapter.raise_on_connect:
            raise RuntimeError("connect failed")
        return (FakeAdapter.current_uidvalidity, dict(FakeAdapter.headers_by_uid))


@pytest.fixture
def store(monkeypatch):
    s = FakeStore()
    config._instances["metadata_store"] = s
    monkeypatch.setattr("adapters.email_imap_source.EmailImapSource", FakeAdapter)
    FakeAdapter.current_uidvalidity = 100
    FakeAdapter.headers_by_uid = {}
    FakeAdapter.raise_on_connect = False
    yield s
    config._instances.pop("metadata_store", None)


@pytest.fixture
def recorded(monkeypatch):
    """Capture calls to the LIVE upsert instead of reimplementing it here."""
    calls: list[dict] = []
    monkeypatch.setattr(
        "services.email_unsubscribe.record_from_headers",
        lambda **kw: calls.append(kw),
    )
    return calls


_NEWSLETTER = {
    "From": "news@shop.example",
    "List-Unsubscribe": "<https://shop.example/u/abc>, <mailto:stop@shop.example>",
    "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
}


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_records_a_historical_newsletter_sender(store, recorded):
    store.add_source("src-A")
    store.add_candidate("src-A", 100, 7, from_addr="news@shop.example")
    FakeAdapter.headers_by_uid = {7: _NEWSLETTER}

    res = bf.backfill_unsubscribe_targets()

    assert res["recorded"] == 1 and res["skipped"] == 0 and res["sources"] == 1
    assert recorded[0]["from_addr"] == "news@shop.example"
    assert recorded[0]["headers"]["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"


def test_it_delegates_to_the_live_upsert_rather_than_writing_its_own_sql(store, recorded):
    """The preserve-on-empty rules exist so a sender that also sends ordinary
    mail does not become un-unsubscribable. A backfill with its own INSERT would
    have to restate them, and would drift."""
    store.add_source("src-A")
    store.add_candidate("src-A", 100, 7, from_addr="news@shop.example")
    FakeAdapter.headers_by_uid = {7: _NEWSLETTER}

    bf.backfill_unsubscribe_targets()

    assert len(recorded) == 1, "must go through record_from_headers, not raw SQL"
    assert set(recorded[0]) == {
        "user_id",
        "source_id",
        "from_addr",
        "from_display",
        "headers",
    }


def test_each_message_is_recorded_so_message_count_is_real(store, recorded):
    """The ticket asks for a real count, 'not 1 per sender'. `record_from_headers`
    increments per call, so the count is real precisely because this calls it
    once per message rather than once per sender."""
    store.add_source("src-A")
    for uid in (7, 8, 9):
        store.add_candidate("src-A", 100, uid, from_addr="news@shop.example")
    FakeAdapter.headers_by_uid = {u: _NEWSLETTER for u in (7, 8, 9)}

    res = bf.backfill_unsubscribe_targets()

    assert res["recorded"] == 3
    assert len(recorded) == 3


# ---------------------------------------------------------------------------
# Idempotence — the property the template got for free and this one does not
# ---------------------------------------------------------------------------


def test_a_sender_already_in_the_list_is_skipped_so_a_rerun_cannot_inflate_the_count(
    store, recorded
):
    """`record_from_headers` adds 1 to `message_count` every call. Without this
    skip, running the backfill twice would double every historical sender's
    count, and there is no marker on `signals` to make it resumable the way
    `from_addr = ''` makes the sender backfill resumable."""
    store.add_source("src-A")
    store.add_candidate("src-A", 100, 7, from_addr="news@shop.example")
    store.add_known_sender("src-A", "news@shop.example")
    FakeAdapter.headers_by_uid = {7: _NEWSLETTER}

    res = bf.backfill_unsubscribe_targets()

    assert res["recorded"] == 0 and res["skipped"] == 1
    assert recorded == []


def test_a_different_sender_on_a_known_source_is_still_recorded(store, recorded):
    """The skip is per SENDER, not per source — one known newsletter must not
    hide every other subscription in the same mailbox."""
    store.add_source("src-A")
    store.add_candidate("src-A", 100, 7, from_addr="known@shop.example")
    store.add_candidate("src-A", 100, 8, from_addr="new@other.example")
    store.add_known_sender("src-A", "known@shop.example")
    FakeAdapter.headers_by_uid = {7: _NEWSLETTER, 8: _NEWSLETTER}

    res = bf.backfill_unsubscribe_targets()

    assert res["recorded"] == 1 and res["skipped"] == 1
    assert recorded[0]["from_addr"] == "new@other.example"


# ---------------------------------------------------------------------------
# Safe skips — every one of these is a skip, never a raise
# ---------------------------------------------------------------------------


def test_skips_uidvalidity_mismatch(store, recorded):
    store.add_source("src-A")
    store.add_candidate("src-A", 99, 7, from_addr="news@shop.example")  # 99 != 100
    FakeAdapter.headers_by_uid = {7: _NEWSLETTER}

    res = bf.backfill_unsubscribe_targets()

    assert res["recorded"] == 0 and res["skipped"] == 1 and recorded == []


def test_skips_a_message_that_is_gone(store, recorded):
    store.add_source("src-A")
    store.add_candidate("src-A", 100, 7, from_addr="news@shop.example")
    FakeAdapter.headers_by_uid = {}  # uid absent from the map

    res = bf.backfill_unsubscribe_targets()

    assert res["recorded"] == 0 and res["skipped"] == 1


def test_an_unreachable_mailbox_is_skipped_not_raised(store, recorded):
    store.add_source("src-A")
    store.add_candidate("src-A", 100, 7, from_addr="news@shop.example")
    FakeAdapter.raise_on_connect = True

    res = bf.backfill_unsubscribe_targets()

    assert res["recorded"] == 0 and res["skipped"] == 1 and res["sources"] == 0


def test_a_non_imap_source_is_skipped(store, recorded):
    store.add_source("src-A", source_type="rss")
    store.add_candidate("src-A", 100, 7, from_addr="news@shop.example")

    res = bf.backfill_unsubscribe_targets()

    assert res["recorded"] == 0 and res["skipped"] == 1


def test_a_malformed_url_is_skipped(store, recorded):
    store.add_source("src-A")
    store.signals.append(
        {
            "signal_id": "sig-bad",
            "source_id": "src-A",
            "url": "https://not-imap/whatever",
            "user_id": "u1",
            "from_addr": "news@shop.example",
            "from_display": "",
        }
    )

    res = bf.backfill_unsubscribe_targets()

    assert res["recorded"] == 0 and res["skipped"] == 1


# ---------------------------------------------------------------------------
# The guarantee this module lives inside
# ---------------------------------------------------------------------------


def test_the_module_contains_no_http_client():
    """`tests/test_unsubscribe_no_outbound_http.py` enforces this across the
    unsubscribe surface; asserted here too because this module is the one that
    handles historical `List-Unsubscribe` URIs in bulk, which is exactly the
    shape that invites a helpful "just fetch them" change."""
    import pathlib

    src = pathlib.Path(bf.__file__).read_text()

    for forbidden in ("httpx", "requests", "urllib.request", "aiohttp"):
        assert forbidden not in src, f"{forbidden} must never appear here"
