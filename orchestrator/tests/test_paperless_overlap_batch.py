# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""LUM-744 — the overlap check, batched.

**The assertion that IS the ticket** is `test_batch_fetches_config_once_...`:
accounts and rules are per paperless *instance* and only the matching is
per-source, so N mailboxes must cost one `fetch_mail_config()`.

**The assertion that keeps it honest** is `test_batch_issues_one_sources_query`.
The first draft of this work claimed "one config fetch regardless of mailbox
count" while looping a single-row `fetch_one` for each mailbox's URL — trading N
HTTP calls for N database round-trips and calling it an improvement. The HTTP
count assertion passes straight through that regression, which is exactly why the
DB count is asserted separately rather than assumed to follow.

Several tests here are **negative space** — they assert something does NOT happen
(no HTTP, no query, no bypass). Two review rounds on this feature found three
defects of the same shape: a code path that runs, returns cleanly, and does
nothing observable. Positive assertions kept passing against them.
"""

from __future__ import annotations

import pytest

import config
from services import external_consumer_guard as ecg
from services import paperless_overlap as po


class _Store:
    """Counts what it was asked, so a test can assert N mailboxes → one query."""

    def __init__(self, urls: dict[str, str] | None = None):
        self.urls = urls or {}
        self.fetch_all_calls: list[tuple] = []
        self.fetch_one_calls: list[tuple] = []

    def fetch_one(self, sql, params=None):
        self.fetch_one_calls.append((sql, params))
        return None

    def fetch_all(self, sql, params=None):
        self.fetch_all_calls.append((sql, params))
        ids = (params or (None, []))[1] or []
        return [{"id": sid, "url": self.urls[sid]} for sid in ids if sid in self.urls]


class _Conn:
    base_url = "https://paperless.lan"
    token = "t"  # noqa: S105 - fixture value, not a credential


class _CountingReader:
    """One instance per `fetch_mail_config()`; the class counts them all."""

    calls = 0

    def __init__(self, conn, deadline_s=None):
        pass

    def fetch_mail_config(self):
        type(self).calls += 1
        return (_CountingReader.accounts, _CountingReader.rules, _CountingReader.status)


def _install(
    monkeypatch,
    *,
    urls,
    accounts=None,
    rules=None,
    status="ok",
    username="me@gmail.com",
    has_paperless=True,
):
    store = _Store(urls)
    monkeypatch.setattr(config, "get_metadata_store", lambda: store)
    monkeypatch.setattr(ecg, "has_external_consumer", lambda _u: has_paperless)
    monkeypatch.setattr(po.paperless_credentials, "load_connection", lambda _u, **_k: _Conn())
    monkeypatch.setattr(po, "_mailbox_username", lambda *_a, **_k: username)

    import adapters.paperless_source as ps

    _CountingReader.calls = 0
    _CountingReader.accounts = accounts or []
    _CountingReader.rules = rules or []
    _CountingReader.status = status
    monkeypatch.setattr(ps, "PaperlessMailConfigReader", _CountingReader)
    return store


GMAIL = "imap://imap.gmail.com:993/{}"
ACCOUNT = {"id": 1, "imap_server": "imap.gmail.com", "username": "me@gmail.com"}
RULE = {"id": 10, "account": 1, "folder": "INBOX", "action": 3, "enabled": True}


def test_batch_fetches_config_once_for_many_sources(monkeypatch):
    """The ticket, in one assertion. Three mailboxes, one paperless fetch."""
    urls = {f"s{i}": GMAIL.format(i) for i in range(3)}
    _install(monkeypatch, urls=urls, accounts=[ACCOUNT], rules=[RULE])

    out = po.overlap_for_sources("u1", list(urls))

    assert _CountingReader.calls == 1, "N mailboxes must cost ONE config fetch"
    assert len(out) == 3
    assert all(r.status == "ok" for r in out.values())


def test_batch_issues_one_sources_query_not_one_per_mailbox(monkeypatch):
    """The DB half of the same claim.

    Without this, looping `source_url_for_imap_instance` satisfies every other
    assertion in this file while shipping N round-trips.
    """
    urls = {f"s{i}": GMAIL.format(i) for i in range(4)}
    store = _install(monkeypatch, urls=urls, accounts=[ACCOUNT], rules=[RULE])

    po.overlap_for_sources("u1", list(urls))

    assert len(store.fetch_all_calls) == 1, "one bulk sources read, not one per mailbox"
    assert store.fetch_one_calls == [], "no single-row reads on the batch path"


def test_wrapper_and_batch_agree_for_one_source(monkeypatch):
    """`overlap_for_source` must stay a wrapper. Two implementations drift, and
    the visible symptom is a gate that fires on one surface and not the other."""
    urls = {"s1": GMAIL.format("personal")}
    _install(monkeypatch, urls=urls, accounts=[ACCOUNT], rules=[RULE])

    single = po.overlap_for_source("u1", "s1")
    batch = po.overlap_for_sources("u1", ["s1"])["s1"]

    assert single == batch
    assert single.overlap is not None and single.overlap.confidence == "high"


def test_one_bad_credential_does_not_sink_the_batch(monkeypatch):
    """A decrypt failure on one mailbox degrades that one to `low`, not the rest.

    `_mailbox_username` already swallows and returns None; this pins that the
    batch inherits that per-source isolation rather than aborting.
    """
    urls = {"a": GMAIL.format("a"), "b": GMAIL.format("b")}
    _install(monkeypatch, urls=urls, accounts=[ACCOUNT], rules=[RULE])
    monkeypatch.setattr(
        po, "_mailbox_username", lambda _u, sid: None if sid == "b" else "me@gmail.com"
    )

    out = po.overlap_for_sources("u1", ["a", "b"])

    assert out["a"].overlap.confidence == "high"
    assert out["b"].overlap.confidence == "low", "no username ⇒ host-only ⇒ low, not absent"


def test_partial_failure_is_per_source_not_global(monkeypatch):
    """An unparseable url takes out its own mailbox and nothing else."""
    urls = {"good": GMAIL.format("g"), "bad": "not-a-url"}
    _install(monkeypatch, urls=urls, accounts=[ACCOUNT], rules=[RULE])

    out = po.overlap_for_sources("u1", ["good", "bad"])

    assert out["good"].overlap is not None
    assert out["bad"].status == "ok" and out["bad"].overlap is None


def test_unavailable_is_not_reported_as_clear(monkeypatch):
    """The invariant every surface depends on: a failed probe is not an all-clear."""
    urls = {"s1": GMAIL.format("p")}
    _install(monkeypatch, urls=urls, accounts=[], rules=[], status="unavailable")

    out = po.overlap_for_sources("u1", ["s1"])["s1"]

    assert out.status == "unavailable"
    assert out.overlap is None


def test_disabled_env_short_circuits_before_any_fetch(monkeypatch):
    urls = {"s1": GMAIL.format("p")}
    _install(monkeypatch, urls=urls, accounts=[ACCOUNT], rules=[RULE])
    monkeypatch.setenv("PAPERLESS_OVERLAP_CHECK_ENABLED", "false")

    out = po.overlap_for_sources("u1", ["s1"])

    assert _CountingReader.calls == 0
    assert out["s1"] == po.OverlapResult(status="ok")


def test_no_paperless_configured_makes_no_http_call(monkeypatch):
    urls = {"s1": GMAIL.format("p")}
    store = _install(monkeypatch, urls=urls, accounts=[ACCOUNT], rules=[RULE], has_paperless=False)

    out = po.overlap_for_sources("u1", ["s1"])

    assert _CountingReader.calls == 0
    assert store.fetch_all_calls == [], "the guard must precede the sources read too"
    assert out["s1"].status == "ok"


def test_empty_source_list_does_nothing_at_all(monkeypatch):
    store = _install(monkeypatch, urls={}, accounts=[ACCOUNT], rules=[RULE])

    assert po.overlap_for_sources("u1", []) == {}
    assert _CountingReader.calls == 0
    assert store.fetch_all_calls == []


def test_duplicate_source_ids_are_deduped(monkeypatch):
    urls = {"s1": GMAIL.format("p")}
    store = _install(monkeypatch, urls=urls, accounts=[ACCOUNT], rules=[RULE])

    out = po.overlap_for_sources("u1", ["s1", "s1", "s1"])

    assert list(out) == ["s1"]
    assert len(store.fetch_all_calls) == 1
    assert _CountingReader.calls == 1


# ── assume_consumer — a guard bypass, tested as one ──────────────────────────


def test_assume_consumer_bypasses_only_the_consumer_check(monkeypatch):
    """With no paperless source row the probe still runs, because the caller is
    in the act of creating that row. Everything else must still apply."""
    urls = {"s1": GMAIL.format("p")}
    _install(monkeypatch, urls=urls, accounts=[ACCOUNT], rules=[RULE], has_paperless=False)

    out = po.overlap_for_sources("u1", ["s1"], assume_consumer=True)

    assert _CountingReader.calls == 1, "the probe must run despite no sources row"
    assert out["s1"].overlap is not None


def test_assume_consumer_does_not_bypass_the_env_switch(monkeypatch):
    """It skips exactly one predicate. An operator who turned the feature off
    must not find it running anyway on one route."""
    urls = {"s1": GMAIL.format("p")}
    _install(monkeypatch, urls=urls, accounts=[ACCOUNT], rules=[RULE], has_paperless=False)
    monkeypatch.setenv("PAPERLESS_OVERLAP_CHECK_ENABLED", "false")

    out = po.overlap_for_sources("u1", ["s1"], assume_consumer=True)

    assert _CountingReader.calls == 0
    assert out["s1"] == po.OverlapResult(status="ok")


def test_assume_consumer_is_keyword_only(monkeypatch):
    """It must never be reachable by a caller that meant to pass a source list."""
    with pytest.raises(TypeError):
        po.overlap_for_sources("u1", ["s1"], True)  # type: ignore[misc]


def test_default_still_short_circuits(monkeypatch):
    """The bypass is opt-in. Omitting it keeps the shipped behaviour."""
    urls = {"s1": GMAIL.format("p")}
    _install(monkeypatch, urls=urls, accounts=[ACCOUNT], rules=[RULE], has_paperless=False)

    po.overlap_for_sources("u1", ["s1"])

    assert _CountingReader.calls == 0


# ── copy ─────────────────────────────────────────────────────────────────────


def test_present_and_future_tense_constants_are_distinct():
    """Three `_EXISTING` constants shipped once with no consumer passing
    `context=existing`, leaving them dead while the page rendered copy about a
    rule the reader was not creating. Pin that they are actually different."""
    assert po.MESSAGE_EXISTING != po.MESSAGE
    assert po.MESSAGE_EXISTING_DELETE != po.MESSAGE_DELETE
    assert po.ADVISORY_EXISTING != po.ADVISORY
    for existing in (po.MESSAGE_EXISTING, po.MESSAGE_EXISTING_DELETE, po.ADVISORY_EXISTING):
        assert "this rule" not in existing, "present tense must not describe a rule being made"


def test_connect_refusal_carries_the_overlap_code_not_the_starvation_one():
    """The defect this replaces: routing the arm through the starvation code (or
    a third new one) renders nothing, because the client matches on `code`."""
    detail = po.connect_refusal_detail([{"source_name": "Work", "rule_count": 3}])

    assert detail["code"] == po.OVERLAP_CODE
    assert detail["code"] != ecg.STARVATION_CODE


def test_connect_refusal_carries_per_mailbox_counts():
    detail = po.connect_refusal_detail(
        [{"source_name": "Work", "rule_count": 3}, {"source_name": "Home", "rule_count": 1}]
    )

    assert detail["mailboxes"] == [
        {"source_name": "Work", "rule_count": 3},
        {"source_name": "Home", "rule_count": 1},
    ]


def test_connect_refusal_leaks_no_internal_state():
    detail = po.connect_refusal_detail([{"source_name": "Work", "rule_count": 3}])
    blob = repr(detail)

    assert "folder" not in blob
    assert "username" not in blob
    assert "source_id" not in blob
