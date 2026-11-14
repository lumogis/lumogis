# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""LUM-657 — sender backfill: re-fetch From for historical email signals.

Hermetic: a fake metadata store answers the candidate/source queries and captures
UPDATEs; the IMAP adapter is replaced with a fake whose ``fetch_headers`` returns
canned headers. Proves the happy path plus every safe-skip branch (UIDVALIDITY
mismatch, message gone, non-imap source, source connect error).
"""

from __future__ import annotations

import pytest

import config
from services import email_sender_backfill as bf


class FakeStore:
    def __init__(self):
        self.signals: list[dict] = []
        self.sources: dict[str, dict] = {}
        self.updates: list[tuple] = []

    def add_candidate(self, signal_id, source_id, uidvalidity, uid):
        self.signals.append(
            {
                "signal_id": signal_id,
                "source_id": source_id,
                "url": f"imap://{source_id}/{uidvalidity}/{uid}",
            }
        )

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
        if "from signals" in query.lower():
            return list(self.signals)
        return []

    def fetch_one(self, query, params=None):
        if "from sources" in query.lower():
            return self.sources.get((params or ("",))[0])
        return None

    def execute(self, query, params=None):
        if query.strip().upper().startswith("UPDATE SIGNALS"):
            from_addr, from_display, signal_id = params
            self.updates.append((signal_id, from_addr, from_display))


class FakeAdapter:
    """Stands in for EmailImapSource — returns canned headers or raises."""

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


def test_backfills_matching_signal(store):
    store.add_source("src-A")
    store.add_candidate("sig1", "src-A", 100, 7)
    FakeAdapter.headers_by_uid = {7: {"From": "Dr. Alice Example <alice@example.com>"}}

    res = bf.backfill_email_senders()
    assert res["updated"] == 1 and res["skipped"] == 0
    assert store.updates == [("sig1", "alice@example.com", "Dr. Alice Example")]


def test_skips_uidvalidity_mismatch(store):
    store.add_source("src-A")
    store.add_candidate("sig1", "src-A", 99, 7)  # stored uidvalidity 99 != current 100
    FakeAdapter.headers_by_uid = {7: {"From": "a@x.com"}}

    res = bf.backfill_email_senders()
    assert res["updated"] == 0 and res["skipped"] == 1 and store.updates == []


def test_skips_message_gone(store):
    store.add_source("src-A")
    store.add_candidate("sig1", "src-A", 100, 7)
    FakeAdapter.headers_by_uid = {}  # uid 7 not returned → gone

    res = bf.backfill_email_senders()
    assert res["updated"] == 0 and res["skipped"] == 1


def test_skips_non_imap_source(store):
    store.add_source("src-A", source_type="rss")
    store.add_candidate("sig1", "src-A", 100, 7)
    res = bf.backfill_email_senders()
    assert res["updated"] == 0 and res["skipped"] == 1


def test_source_connect_error_skips_not_raises(store):
    store.add_source("src-A")
    store.add_candidate("sig1", "src-A", 100, 7)
    FakeAdapter.raise_on_connect = True
    res = bf.backfill_email_senders()  # must not raise
    assert res["updated"] == 0 and res["skipped"] == 1 and res["sources"] == 0


def test_parse_imap_url():
    assert bf._parse_imap_url("imap://abc/100/7") == ("abc", 100, 7)
    assert bf._parse_imap_url("https://x/y") is None
    assert bf._parse_imap_url("imap://bad") is None
