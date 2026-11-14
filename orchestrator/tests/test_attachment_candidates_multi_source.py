# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Cross-mailbox candidate selection (LUM-742).

Three properties, each of which the obvious implementation gets wrong.

**Every mailbox gets a share.** The first design fetched from each mailbox,
merged newest-first and took ``limit``. That reads like fair-share and is not:
a mailbox whose mail is simply more recent takes every slot. The test below
pins the case that breaks it — mailbox A's rows are *all newer* than B's — so it
cannot be made green by choosing friendlier fixture timestamps.

**The quota denominator is the caller's mailbox count.** ``_opted_in_sources``
had no ``user_id`` predicate, so a denominator taken from its length would size
one household's page by other households' mailbox counts.

**A candidate knows its mailbox.** A filing rule belongs to exactly one mailbox,
so a group keyed on sender alone has no rule "Always file from this sender"
could create — which is why that button vanished on any unscoped list, taking
the LUM-728 paperless gate with it.
"""

from __future__ import annotations

import pytest

import config
from services import email_attachment_ingest as svc

A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"


class _Store:
    """Serves the opted-in-sources query, the candidates query, and the COUNT.

    Candidate rows are keyed by source so a test can say "A has 500, all newer
    than B's 3" without hand-building SQL results.
    """

    def __init__(self, sources, per_source):
        self.sources = sources
        self.per_source = per_source
        self.count_calls: list[str] = []
        self.raise_for: set[str] = set()

    def fetch_all(self, sql, params=None):
        p = params or ()
        if "FROM sources" in sql and "attachment_ingest_enabled" in sql:
            rows = list(self.sources)
            if "user_id = %s" in sql:
                rows = [r for r in rows if r["user_id"] == p[0]]
            return rows
        if "FROM email_attachment ea" in sql:
            source_id, user_id = str(p[0]), p[1]
            if source_id in self.raise_for:
                raise RuntimeError("mailbox query blew up")
            limit = int(p[-1])
            rows = [r for r in self.per_source.get(source_id, []) if r["_user_id"] == user_id]
            return rows[:limit]
        return []

    def fetch_one(self, sql, params=None):
        if "COUNT(*)" in sql:
            source_id, user_id = str((params or ())[0]), (params or ())[1]
            self.count_calls.append(source_id)
            rows = [r for r in self.per_source.get(source_id, []) if r["_user_id"] == user_id]
            return {"n": len(rows)}
        return None


def _row(source_id, i, *, user_id="u1", sender="billing@utility.example", when="2026-07-01"):
    return {
        "_user_id": user_id,
        "signal_id": f"{source_id[:4]}-sig-{i}",
        "part_index": 0,
        "filename": f"doc{i}.pdf",
        "mime_type": "application/pdf",
        "size_bytes": 100,
        "signal_url": "imap://mailbox/INBOX/1",
        "source_id": source_id,
        "source_name": "work@example.com" if source_id == A else "home@example.com",
        "from_addr": sender,
        "from_display": "Utility",
        "title": "Invoice",
        "category": "bills",
        "received_at": when,
    }


def _source(source_id, *, user_id="u1", name=None, method="imap_full_ingest"):
    return {
        "id": source_id,
        "user_id": user_id,
        "name": name or ("work@example.com" if source_id == A else "home@example.com"),
        "attachment_ingest_since": "2026-01-01",
        "extraction_method": method,
    }


@pytest.fixture
def store(monkeypatch):
    st = _Store([], {})
    monkeypatch.setattr(config, "get_metadata_store", lambda: st)
    # The selection query filters on the extractor registry; without this every
    # query short-circuits to an empty list and every test below passes vacuously.
    monkeypatch.setattr(svc, "ingestible_extensions", lambda: frozenset({".pdf"}))
    monkeypatch.setattr(svc, "max_ingest_bytes", lambda: 10_000_000)
    return st


# --- source identity ---------------------------------------------------------


def test_a_candidate_carries_the_mailbox_it_came_from(store):
    store.sources = [_source(A)]
    store.per_source = {A: [_row(A, 1)]}

    page = svc.candidates_page_for_user(user_id="u1", limit=10)

    assert page.candidates[0].source_id == A
    assert page.candidates[0].source_name == "work@example.com"


# --- fair share --------------------------------------------------------------


def test_quota_guarantees_every_source_a_share_even_when_one_is_all_newer(store):
    """The case the merge design silently failed.

    Mailbox A has 500 candidates and **every one is newer** than mailbox B's
    three. Merging newest-first and taking 100 returns 100 from A and nothing
    from B, and B's owner concludes filing is broken there.
    """
    store.sources = [_source(A), _source(B)]
    store.per_source = {
        A: [_row(A, i, when="2026-07-20") for i in range(500)],
        B: [_row(B, i, when="2026-01-05") for i in range(3)],
    }

    page = svc.candidates_page_for_user(user_id="u1", limit=100)

    by_source = {}
    for c in page.candidates:
        by_source.setdefault(c.source_id, 0)
        by_source[c.source_id] += 1
    assert by_source[B] == 3, "the older mailbox must not be starved to zero"
    assert by_source[A] == 50, "the busy mailbox takes its quota, not the whole page"


def test_the_page_never_exceeds_the_limit_when_sources_do_not_divide_it(store):
    store.sources = [_source(A), _source(B)]
    store.per_source = {
        A: [_row(A, i) for i in range(50)],
        B: [_row(B, i) for i in range(50)],
    }

    page = svc.candidates_page_for_user(user_id="u1", limit=7)

    assert len(page.candidates) == 7


def test_quota_never_allocates_zero_to_a_source(store):
    """`limit // n` floors to 0 for limit=1, n=3 — every mailbox would vanish."""
    C = "cccccccc-cccc-cccc-cccc-cccccccccccc"
    store.sources = [_source(A), _source(B), _source(C, name="third")]
    store.per_source = {s: [_row(s, 1)] for s in (A, B, C)}

    page = svc.candidates_page_for_user(user_id="u1", limit=1)

    assert len(page.candidates) >= 1


def test_truncated_sources_names_the_mailbox_with_more(store):
    store.sources = [_source(A), _source(B)]
    store.per_source = {
        A: [_row(A, i) for i in range(500)],
        B: [_row(B, i) for i in range(2)],
    }

    page = svc.candidates_page_for_user(user_id="u1", limit=100)

    assert [s for s, _ in page.truncated_sources] == [A]
    assert page.truncated_sources[0][1] == "work@example.com", "name it, do not just id it"


# --- the pending count -------------------------------------------------------


def test_pending_total_is_not_capped_by_the_page_limit(store):
    """`total` is the trimmed page length, so a badge reading it shows 1 forever."""
    store.sources = [_source(A)]
    store.per_source = {A: [_row(A, i) for i in range(250)]}

    page = svc.candidates_page_for_user(user_id="u1", limit=1)

    assert len(page.candidates) == 1
    assert page.pending_total == 250


# --- isolation ---------------------------------------------------------------


def test_another_users_attachments_never_appear(store):
    store.sources = [_source(A), _source(B, user_id="mallory")]
    store.per_source = {
        A: [_row(A, 1)],
        B: [_row(B, i, user_id="mallory") for i in range(5)],
    }

    page = svc.candidates_page_for_user(user_id="u1", limit=100)

    assert {c.source_id for c in page.candidates} == {A}


def test_the_quota_denominator_counts_only_the_callers_mailboxes(store):
    """Another household's mailboxes must not shrink this caller's share."""
    others = [_source(f"dddddddd-dddd-dddd-dddd-{i:012d}", user_id="mallory") for i in range(9)]
    store.sources = [_source(A)] + others
    store.per_source = {A: [_row(A, i) for i in range(100)]}

    page = svc.candidates_page_for_user(user_id="u1", limit=100)

    assert len(page.candidates) == 100, "n must be 1 here, not 10"


# --- degenerate and failure states -------------------------------------------


def test_no_eligible_sources_returns_an_empty_page_rather_than_dividing_by_zero(store):
    """This is the activation branch, not an edge case — it is the most-hit
    state on a fresh install, and `limit // 0` raises there."""
    store.sources = []

    page = svc.candidates_page_for_user(user_id="u1", limit=100)

    assert page.candidates == [] and page.pending_total == 0
    assert page.truncated_sources == [] and page.errored_sources == []


def test_a_metadata_mode_mailbox_is_not_eligible(store):
    store.sources = [_source(A, method="imap_metadata")]
    store.per_source = {A: [_row(A, 1)]}

    page = svc.candidates_page_for_user(user_id="u1", limit=100)

    assert page.candidates == []


def test_one_failing_mailbox_is_reported_not_swallowed(store):
    """A silently-absent mailbox reads as a broken feature; the other mailbox
    must still return its candidates."""
    store.sources = [_source(A), _source(B)]
    store.per_source = {A: [_row(A, 1)], B: [_row(B, 1)]}
    store.raise_for = {A}

    page = svc.candidates_page_for_user(user_id="u1", limit=100)

    assert [s for s, _ in page.errored_sources] == [A]
    assert {c.source_id for c in page.candidates} == {B}


# --- the accept/decline path must not move -----------------------------------


def test_a_failing_count_does_not_discard_that_mailboxs_candidates(store):
    """The count decorates a tab; the list is the product.

    Sharing one try-block meant a COUNT failure threw away candidates that had
    already been fetched successfully.
    """
    store.sources = [_source(A)]
    store.per_source = {A: [_row(A, 1)]}
    original = svc.pending_count_for_source

    def boom(**kw):
        raise RuntimeError("count blew up")

    svc.pending_count_for_source = boom
    try:
        page = svc.candidates_page_for_user(user_id="u1", limit=100)
    finally:
        svc.pending_count_for_source = original

    assert len(page.candidates) == 1, "a bad count must not cost the user their list"
    assert page.errored_sources == [], "the list succeeded; do not report the mailbox as failed"


def test_candidates_for_user_still_returns_a_plain_list(store):
    """Signature kept for callers that still want a plain list.

    Accept/decline no longer build their index from this — see
    ``test_candidate_index_matches_fair_shared_page``.
    """
    store.sources = [_source(A)]
    store.per_source = {A: [_row(A, 1)]}

    out = svc.candidates_for_user(user_id="u1", limit=10)

    assert isinstance(out, list)
    assert out and isinstance(out[0], svc.Candidate)


def test_candidate_index_matches_fair_shared_page(store):
    """LUM-742 regression: the page and accept/decline must agree on which refs exist.

    ``candidates_for_user`` fills sequentially until the limit; a busy mailbox
    can consume the whole ``_candidate_index`` budget and starve a sibling that
    the fair-shared page still shows — filing then returns ``not_found`` for
    rows the user is looking at.
    """
    store.sources = [_source(A), _source(B)]
    store.per_source = {
        A: [_row(A, i, when="2026-07-20") for i in range(10_001)],
        B: [_row(B, i, when="2026-01-05") for i in range(3)],
    }

    page = svc.candidates_page_for_user(user_id="u1", limit=100)
    b_on_page = [c for c in page.candidates if c.source_id == B]
    assert len(b_on_page) == 3, "precondition: the fair-shared page shows mailbox B"

    index = svc._candidate_index("u1")
    for c in b_on_page:
        assert (c.signal_id, c.part_index) in index, (
            "accept/decline must see every candidate the review surface shows"
        )


def test_a_scoped_request_returns_only_that_mailbox(store):
    store.sources = [_source(A), _source(B)]
    store.per_source = {A: [_row(A, 1)], B: [_row(B, 1)]}

    page = svc.candidates_page_for_user(user_id="u1", source_id=B, limit=100)

    assert {c.source_id for c in page.candidates} == {B}
