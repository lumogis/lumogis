# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""The attachment auto-ingest routes (LUM-702).

Two contracts carry most of the weight.

**404, never 403.** A 403 on another user's mailbox or ledger row confirms it
exists, which turns the ownership gate into an oracle over someone else's mail.
The whole email family holds this posture.

**422 on a metadata-mode mailbox.** Such a mailbox never fetched bodies, so
``email_attachment`` has no rows for it — enabling the toggle would be a silent
no-op the user reads as a broken feature. The message names full ingest so it is
actionable rather than merely correct.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from routes.api_v1 import email_attachment_ingest as mod

import config

SOURCE = "11111111-1111-1111-1111-111111111111"
OTHER = "22222222-2222-2222-2222-222222222222"


class _Store:
    def __init__(self, *, method="imap_full_ingest", owner="u1"):
        self.method = method
        self.owner = owner
        self.executed: list[tuple] = []
        self.rows: list[dict] = []

    def fetch_one(self, sql, params=None):
        if "FROM sources" in sql:
            source_id, user_id = params[0], params[1]
            if source_id != SOURCE or user_id != self.owner:
                return None
            return {"id": SOURCE, "extraction_method": self.method}
        return None

    def fetch_all(self, sql, params=None):
        return list(self.rows)

    def execute(self, sql, params=None):
        self.executed.append((sql, params))


def _client(store, *, user_id="u1"):
    from auth import UserContext

    ctx = UserContext(user_id=user_id, role="member", allows_shared=True)
    app = FastAPI()
    app.include_router(mod.router)
    app.dependency_overrides[mod.require_user] = lambda: ctx
    app.dependency_overrides[mod.require_same_origin] = lambda: None
    return TestClient(app)


@pytest.fixture
def store(monkeypatch):
    st = _Store()
    monkeypatch.setattr(config, "get_metadata_store", lambda: st)
    return st


# --- opt-in -----------------------------------------------------------------


def test_enabling_records_the_opt_in_and_a_go_forward_watermark(store):
    r = _client(store).patch(
        f"/api/v1/email/sources/{SOURCE}/attachment-ingest",
        json={"enabled": True},
    )

    assert r.status_code == 200
    sql, params = store.executed[-1]
    assert "attachment_ingest_enabled" in sql
    assert "COALESCE(attachment_ingest_since, NOW())" in sql, "go-forward by default"
    assert params[0] is True


def test_apply_to_existing_moves_the_watermark_to_epoch(store):
    r = _client(store).patch(
        f"/api/v1/email/sources/{SOURCE}/attachment-ingest",
        json={"enabled": True, "apply_to_existing": True},
    )

    assert r.status_code == 200
    assert r.json()["apply_to_existing"] is True
    sql, _ = store.executed[-1]
    assert "'epoch'::timestamptz" in sql


def test_disabling_leaves_the_watermark_alone(store):
    """Re-enabling later must not silently re-sweep everything since the first
    opt-in."""
    _client(store).patch(
        f"/api/v1/email/sources/{SOURCE}/attachment-ingest",
        json={"enabled": False},
    )

    sql, params = store.executed[-1]
    assert "ELSE attachment_ingest_since END" in sql
    assert params[0] is False


# --- 28 ---------------------------------------------------------------------


def test_enabling_on_a_metadata_mode_mailbox_is_422(monkeypatch):
    st = _Store(method="imap_metadata")
    monkeypatch.setattr(config, "get_metadata_store", lambda: st)

    r = _client(st).patch(
        f"/api/v1/email/sources/{SOURCE}/attachment-ingest",
        json={"enabled": True},
    )

    assert r.status_code == 422
    detail = r.json()["detail"]
    assert detail["error"] == "full_ingest_required"
    assert "full ingest" in detail["message"].lower(), (
        "a silent no-op reads as a broken feature — the message must say what to do"
    )
    assert st.executed == [], "nothing may be written for a mailbox that cannot use it"


# --- 27 ---------------------------------------------------------------------


def test_another_users_mailbox_cannot_be_opted_in(store):
    r = _client(store, user_id="mallory").patch(
        f"/api/v1/email/sources/{SOURCE}/attachment-ingest",
        json={"enabled": True},
    )

    assert r.status_code == 404, "403 would confirm another user's mailbox exists"
    assert store.executed == []


def test_an_unknown_mailbox_is_404(store):
    r = _client(store).patch(
        f"/api/v1/email/sources/{OTHER}/attachment-ingest",
        json={"enabled": True},
    )

    assert r.status_code == 404


# --- filed list -------------------------------------------------------------


def test_the_filed_list_returns_filings_and_skips(store):
    store.rows = [
        {
            "id": 1,
            "signal_id": "email:s:1",
            "part_index": 0,
            "filename": "invoice.pdf",
            "mime_type": "application/pdf",
            "size_bytes": 120,
            "logical_path": "email-attachment://x/1/0",
            "ingested_at": "2026-07-26T00:00:00Z",
            "reversed_at": None,
            "skipped_reason": None,
        },
        {
            "id": 2,
            "signal_id": "email:s:2",
            "part_index": 0,
            "filename": "photo.heic",
            "mime_type": "image/heic",
            "size_bytes": 90,
            "logical_path": "email-attachment://x/2/0",
            "ingested_at": "2026-07-26T00:00:00Z",
            "reversed_at": None,
            "skipped_reason": "no_extractor",
        },
    ]

    body = _client(store).get("/api/v1/email/attachment-ingest/filed").json()

    assert body["total"] == 2
    assert body["rows"][1]["skipped_reason"] == "no_extractor", (
        "skips are how the UI explains an absence"
    )
    assert body["rows"][0]["skipped_reason"] is None


def test_filtering_the_filed_list_by_another_users_mailbox_is_404(store):
    r = _client(store, user_id="mallory").get(
        f"/api/v1/email/attachment-ingest/filed?source_id={SOURCE}"
    )

    assert r.status_code == 404


def test_the_filed_list_limit_is_bounded(store):
    r = _client(store).get("/api/v1/email/attachment-ingest/filed?limit=5000")

    assert r.status_code == 422


# --- undo -------------------------------------------------------------------


def test_undo_by_log_id_reports_what_landed(store, monkeypatch):
    monkeypatch.setattr(mod.ingest_svc, "undo_row", lambda u, i: True)

    body = _client(store).post("/api/v1/email/attachment-ingest/undo", json={"log_id": 7}).json()

    assert body["reversed_count"] == 1


# --- 26 ---------------------------------------------------------------------


def test_another_users_log_row_is_404_not_403(store, monkeypatch):
    """The service resolves ownership, so a foreign row simply reverses nothing.

    No 403 is ever emitted, and the caller cannot distinguish "not yours" from
    "already reversed" — which is the point.
    """
    seen: list = []

    def _undo(user_id, log_id):
        seen.append(user_id)
        return False  # not this caller's row

    monkeypatch.setattr(mod.ingest_svc, "undo_row", _undo)

    r = _client(store, user_id="mallory").post(
        "/api/v1/email/attachment-ingest/undo", json={"log_id": 1}
    )

    assert r.status_code != 403
    assert r.json()["reversed_count"] == 0
    assert seen == ["mallory"], "the caller's own id scopes the reversal"


def test_undo_by_source_returns_the_count(store, monkeypatch):
    monkeypatch.setattr(mod.ingest_svc, "undo_for_source", lambda u, s: 4)

    body = (
        _client(store)
        .post("/api/v1/email/attachment-ingest/undo", json={"source_id": SOURCE})
        .json()
    )

    assert body["reversed_count"] == 4


def test_undo_by_another_users_source_is_404(store, monkeypatch):
    monkeypatch.setattr(mod.ingest_svc, "undo_for_source", lambda u, s: 4)

    r = _client(store, user_id="mallory").post(
        "/api/v1/email/attachment-ingest/undo", json={"source_id": SOURCE}
    )

    assert r.status_code == 404


@pytest.mark.parametrize(
    "body",
    [{}, {"log_id": 1, "source_id": SOURCE}],
)
def test_neither_or_both_undo_fields_is_422(store, body):
    r = _client(store).post("/api/v1/email/attachment-ingest/undo", json=body)

    assert r.status_code == 422


def test_the_filed_list_carries_the_sender_and_subject(store):
    """Where a library document came from is the question this surface answers."""
    store.rows = [
        {
            "id": 1,
            "signal_id": "email:s:1",
            "part_index": 0,
            "filename": "invoice.pdf",
            "mime_type": "application/pdf",
            "size_bytes": 120,
            "logical_path": "email-attachment://x/1/0",
            "ingested_at": "2026-07-26T00:00:00Z",
            "reversed_at": None,
            "skipped_reason": None,
            "from_addr": "accounts@vendor.example",
            "from_display": "Vendor Accounts",
            "title": "Your July invoice",
        }
    ]

    row = _client(store).get("/api/v1/email/attachment-ingest/filed").json()["rows"][0]

    assert row["from_addr"] == "accounts@vendor.example"
    assert row["from_display"] == "Vendor Accounts"
    assert row["subject"] == "Your July invoice"


def test_a_filing_whose_signal_was_purged_still_lists(store):
    """The ledger keeps no FK on signal_id (matching 066), so the LEFT JOIN can
    miss — the filing must survive its provenance."""
    store.rows = [
        {
            "id": 1,
            "signal_id": "email:s:gone",
            "part_index": 0,
            "filename": "invoice.pdf",
            "mime_type": "application/pdf",
            "size_bytes": 120,
            "logical_path": "email-attachment://x/1/0",
            "ingested_at": "2026-07-26T00:00:00Z",
            "reversed_at": None,
            "skipped_reason": None,
            "from_addr": None,
            "from_display": None,
            "title": None,
        }
    ]

    row = _client(store).get("/api/v1/email/attachment-ingest/filed").json()["rows"][0]

    assert row["filename"] == "invoice.pdf"
    assert row["from_addr"] == ""


# --- LUM-721 — re-file ------------------------------------------------------


@pytest.mark.parametrize("outcome", ["refiled", "not_found", "duplicate", "unavailable"])
def test_refile_reports_the_outcome_and_never_4xxs(store, monkeypatch, outcome):
    """Always 200. "not your row", "never filed" and "not currently reversed" are
    indistinguishable on purpose, so this cannot probe another user's ledger."""
    monkeypatch.setattr(mod.ingest_svc, "refile_row", lambda u, i: outcome)

    r = _client(store).post("/api/v1/email/attachment-ingest/refile", json={"log_id": 7})

    assert r.status_code == 200
    assert r.json()["outcome"] == outcome


def test_refile_scopes_to_the_caller(store, monkeypatch):
    seen: list = []
    monkeypatch.setattr(
        mod.ingest_svc, "refile_row", lambda u, i: seen.append((u, i)) or "not_found"
    )

    _client(store, user_id="mallory").post(
        "/api/v1/email/attachment-ingest/refile", json={"log_id": 1}
    )

    assert seen == [("mallory", 1)], "the caller's own id scopes the lookup"


def test_refile_requires_a_log_id(store):
    r = _client(store).post("/api/v1/email/attachment-ingest/refile", json={})

    assert r.status_code == 422


# --------------------------------------------------------------------------- #
# Candidates and rules — the per-attachment decision (LUM-727)
# --------------------------------------------------------------------------- #
#
# These routes carry the same 404-never-403 posture as the rest of the file, and
# one contract of their own: **only `accept` is allowed to spend anything.**
# Listing and declining are pure database work over rows that already exist, so
# the attachments a user says no to cost nothing at all. That is what makes a
# review list over a large, mostly-uninteresting stream affordable.


def _candidate(**kw):
    from services.email_attachment_ingest import Candidate

    base = dict(
        signal_id="sig-1",
        part_index=0,
        filename="invoice.pdf",
        mime_type="application/pdf",
        size_bytes=1024,
        from_addr="billing@utility.example",
        from_display="Utility Billing",
        subject="Your July invoice",
        category="bills",
        received_at="2026-07-01T09:00:00Z",
        signal_url="imap://mailbox/INBOX/17",
    )
    base.update(kw)
    return Candidate(**base)


def _page(candidates, *, truncated=(), errored=(), pending_total=None):
    """LUM-742: the route now consumes a `CandidatePage`, not a bare list.

    Paging, fair-share and truncation moved into the service so the route
    cannot run a second, competing trim on top of the per-source one.
    """
    from services.email_attachment_ingest import CandidatePage

    return CandidatePage(
        candidates=list(candidates),
        truncated_sources=list(truncated),
        errored_sources=list(errored),
        pending_total=len(candidates) if pending_total is None else pending_total,
    )


# --- listing candidates ------------------------------------------------------


def test_candidates_are_grouped_by_sender(store, monkeypatch):
    monkeypatch.setattr(
        mod.ingest_svc,
        "candidates_page_for_user",
        lambda **kw: _page(
            [
                _candidate(signal_id="a"),
                _candidate(signal_id="b"),
                _candidate(signal_id="c", from_addr="hr@work.example", from_display="HR"),
            ]
        ),
    )

    body = _client(store).get("/api/v1/email/attachment-ingest/candidates").json()

    assert [g["from_addr"] for g in body["groups"]] == [
        "billing@utility.example",
        "hr@work.example",
    ]
    assert [g["count"] for g in body["groups"]] == [2, 1]
    assert body["total"] == 3


def test_the_biggest_sender_comes_first(store, monkeypatch):
    """Deciding once for the sender who sends most is the largest single
    reduction in the size of the list, so it is the first thing offered."""
    monkeypatch.setattr(
        mod.ingest_svc,
        "candidates_page_for_user",
        lambda **kw: _page(
            [
                _candidate(signal_id="a", from_addr="rare@x.example"),
                _candidate(signal_id="b", from_addr="often@y.example"),
                _candidate(signal_id="c", from_addr="often@y.example"),
            ]
        ),
    )

    groups = _client(store).get("/api/v1/email/attachment-ingest/candidates").json()["groups"]

    assert groups[0]["from_addr"] == "often@y.example"


def test_a_candidate_never_carries_its_imap_url_to_the_client(store, monkeypatch):
    """``signal_url`` is how the server refetches the part. The client picks
    attachments by (signal_id, part_index) and has no use for it; shipping it
    would hand out mailbox coordinates for no reason."""
    monkeypatch.setattr(
        mod.ingest_svc, "candidates_page_for_user", lambda **kw: _page([_candidate()])
    )

    body = _client(store).get("/api/v1/email/attachment-ingest/candidates").json()

    assert "signal_url" not in body["groups"][0]["candidates"][0]


def test_the_candidate_list_is_bounded_and_says_when_it_truncated(store, monkeypatch):
    """Truncation is reported, never silent — a user who cannot see the rest
    must at least know there is a rest."""
    monkeypatch.setattr(
        mod.ingest_svc,
        "candidates_page_for_user",
        # The service now trims to `limit` and reports WHICH mailbox has more —
        # "your window filled up" must be distinguishable from "nothing here".
        lambda **kw: _page(
            [_candidate(signal_id=f"s{i}") for i in range(2)],
            truncated=[(SOURCE, "work@example.com")],
            pending_total=3,
        ),
    )

    body = _client(store).get("/api/v1/email/attachment-ingest/candidates?limit=2").json()

    assert body["total"] == 2 and body["truncated"] is True
    assert body["truncated_sources"] == [{"source_id": SOURCE, "source_name": "work@example.com"}]
    assert body["pending_total"] == 3, "the count must not be capped by the page limit"


def test_an_untruncated_list_says_so(store, monkeypatch):
    monkeypatch.setattr(
        mod.ingest_svc, "candidates_page_for_user", lambda **kw: _page([_candidate()])
    )

    body = _client(store).get("/api/v1/email/attachment-ingest/candidates?limit=50").json()

    assert body["truncated"] is False


def test_the_candidate_limit_is_capped(store):
    r = _client(store).get("/api/v1/email/attachment-ingest/candidates?limit=100000")

    assert r.status_code == 422


def test_filtering_candidates_by_another_users_mailbox_is_404(store, monkeypatch):
    monkeypatch.setattr(mod.ingest_svc, "candidates_page_for_user", lambda **kw: _page([]))

    r = _client(store).get(f"/api/v1/email/attachment-ingest/candidates?source_id={OTHER}")

    assert r.status_code == 404


def test_candidates_are_scoped_to_the_caller(store, monkeypatch):
    seen: list = []
    monkeypatch.setattr(
        mod.ingest_svc,
        "candidates_page_for_user",
        lambda **kw: seen.append(kw) or _page([]),
    )

    _client(store, user_id="mallory").get("/api/v1/email/attachment-ingest/candidates")

    assert seen[0]["user_id"] == "mallory"


# --- accept ------------------------------------------------------------------


def test_accepting_reports_what_it_filed_and_what_it_did_not(store, monkeypatch):
    monkeypatch.setattr(
        mod.ingest_svc,
        "accept_candidates",
        lambda u, refs, limit: {
            "accepted": 1,
            "skipped": [{"signal_id": "b", "part_index": 0, "reason": "unavailable"}],
        },
    )

    body = (
        _client(store)
        .post(
            "/api/v1/email/attachment-ingest/candidates/accept",
            json={
                "refs": [{"signal_id": "a", "part_index": 0}, {"signal_id": "b", "part_index": 0}]
            },
        )
        .json()
    )

    assert body["accepted"] == 1
    assert body["skipped"][0]["reason"] == "unavailable"


def test_a_bulk_accept_is_bounded_on_the_request_path(store, monkeypatch):
    """N accepted files is N fetch+extract+embed cycles, synchronously. The
    route passes its own ceiling rather than trusting the body's length."""
    seen: list = []
    monkeypatch.setattr(
        mod.ingest_svc,
        "accept_candidates",
        lambda u, refs, limit: seen.append(limit) or {"accepted": 0, "skipped": []},
    )

    _client(store).post(
        "/api/v1/email/attachment-ingest/candidates/accept",
        json={"refs": [{"signal_id": "a", "part_index": 0}]},
    )

    assert seen == [mod._MAX_ACCEPT_PER_CALL]


def test_an_oversized_accept_body_is_refused_before_any_work(store):
    r = _client(store).post(
        "/api/v1/email/attachment-ingest/candidates/accept",
        json={"refs": [{"signal_id": f"s{i}", "part_index": 0} for i in range(201)]},
    )

    assert r.status_code == 422


def test_accepting_is_scoped_to_the_caller(store, monkeypatch):
    seen: list = []
    monkeypatch.setattr(
        mod.ingest_svc,
        "accept_candidates",
        lambda u, refs, limit: seen.append(u) or {"accepted": 0, "skipped": []},
    )

    _client(store, user_id="mallory").post(
        "/api/v1/email/attachment-ingest/candidates/accept",
        json={"refs": [{"signal_id": "a", "part_index": 0}]},
    )

    assert seen == ["mallory"]


# --- decline -----------------------------------------------------------------


def test_declining_reports_the_count(store, monkeypatch):
    monkeypatch.setattr(mod.ingest_svc, "decline_candidates", lambda u, refs: len(refs))

    body = (
        _client(store)
        .post(
            "/api/v1/email/attachment-ingest/candidates/decline",
            json={
                "refs": [{"signal_id": "a", "part_index": 0}, {"signal_id": "b", "part_index": 1}]
            },
        )
        .json()
    )

    assert body["declined"] == 2


def test_declining_is_scoped_to_the_caller(store, monkeypatch):
    seen: list = []
    monkeypatch.setattr(mod.ingest_svc, "decline_candidates", lambda u, refs: seen.append(u) or 0)

    _client(store, user_id="mallory").post(
        "/api/v1/email/attachment-ingest/candidates/decline",
        json={"refs": [{"signal_id": "a", "part_index": 0}]},
    )

    assert seen == ["mallory"]


def test_declining_nothing_is_not_an_error(store, monkeypatch):
    """An empty selection is a no-op, not a 422 — the web sends what is checked,
    and an accidental empty submit should do nothing quietly."""
    monkeypatch.setattr(mod.ingest_svc, "decline_candidates", lambda u, refs: 0)

    r = _client(store).post("/api/v1/email/attachment-ingest/candidates/decline", json={"refs": []})

    assert r.status_code == 200 and r.json()["declined"] == 0


# --- undo a decline ----------------------------------------------------------


def test_undoing_a_decline_reports_whether_it_restored_one(store, monkeypatch):
    monkeypatch.setattr(mod.ingest_svc, "undo_decline", lambda u, s, p: True)

    body = (
        _client(store)
        .post(
            "/api/v1/email/attachment-ingest/candidates/undo-decline",
            json={"signal_id": "a", "part_index": 0},
        )
        .json()
    )

    assert body["restored"] is True


def test_undoing_another_users_decline_reports_nothing_restored_not_403(store, monkeypatch):
    """The service scopes on user_id and simply finds no row. A 403 would confirm
    the row exists, which is the oracle this family refuses to be."""
    monkeypatch.setattr(mod.ingest_svc, "undo_decline", lambda u, s, p: u == "u1")

    r = _client(store, user_id="mallory").post(
        "/api/v1/email/attachment-ingest/candidates/undo-decline",
        json={"signal_id": "a", "part_index": 0},
    )

    assert r.status_code == 200 and r.json()["restored"] is False


def test_undo_decline_requires_both_coordinates(store):
    r = _client(store).post(
        "/api/v1/email/attachment-ingest/candidates/undo-decline", json={"signal_id": "a"}
    )

    assert r.status_code == 422


# --- rules -------------------------------------------------------------------


def test_listing_rules_returns_them_with_their_staleness(store, monkeypatch):
    monkeypatch.setattr(
        mod.rules_svc,
        "list_rules",
        lambda u, s: [
            {
                "id": "r1",
                "match_type": "sender",
                "match_value": "billing@utility.example",
                "created_at": "2026-07-01T00:00:00Z",
                "stale": False,
            },
            {
                "id": "r2",
                "match_type": "category",
                "match_value": "gone",
                "created_at": "2026-07-01T00:00:00Z",
                "stale": True,
            },
        ],
    )

    rules = _client(store).get(f"/api/v1/email/attachment-rules?source_id={SOURCE}").json()["rules"]

    assert [r["stale"] for r in rules] == [False, True]


def test_listing_another_users_rules_is_404(store, monkeypatch):
    monkeypatch.setattr(mod.rules_svc, "list_rules", lambda u, s: [])

    r = _client(store).get(f"/api/v1/email/attachment-rules?source_id={OTHER}")

    assert r.status_code == 404


def test_listing_rules_requires_a_mailbox(store):
    r = _client(store).get("/api/v1/email/attachment-rules")

    assert r.status_code == 422


def test_creating_a_rule_returns_201(store, monkeypatch):
    monkeypatch.setattr(
        mod.rules_svc,
        "create_rule",
        lambda u, s, mt, mv: {"id": "r1", "match_type": mt, "match_value": mv},
    )

    r = _client(store).post(
        "/api/v1/email/attachment-rules",
        json={"source_id": SOURCE, "match_type": "sender", "match_value": "a@b.example"},
    )

    assert r.status_code == 201 and r.json()["id"] == "r1"


def test_creating_a_rule_on_another_users_mailbox_is_404(store, monkeypatch):
    called: list = []
    monkeypatch.setattr(mod.rules_svc, "create_rule", lambda u, s, mt, mv: called.append(1) or {})

    r = _client(store).post(
        "/api/v1/email/attachment-rules",
        json={"source_id": OTHER, "match_type": "sender", "match_value": "a@b.example"},
    )

    assert r.status_code == 404
    assert called == [], "ownership is checked BEFORE the write, not after"


def test_a_duplicate_rule_is_409_not_a_silent_success(store, monkeypatch):
    """Reporting 201 for a rule that was not created would tell the user their
    second, differently-intended rule exists when it does not."""

    def _boom(u, s, mt, mv):
        raise FileExistsError("already exists")

    monkeypatch.setattr(mod.rules_svc, "create_rule", _boom)

    r = _client(store).post(
        "/api/v1/email/attachment-rules",
        json={"source_id": SOURCE, "match_type": "sender", "match_value": "a@b.example"},
    )

    assert r.status_code == 409 and r.json()["detail"]["error"] == "rule_exists"


def test_a_rule_we_refuse_is_422_and_says_why(store, monkeypatch):
    def _boom(u, s, mt, mv):
        raise ValueError("unknown category: 'nope'")

    monkeypatch.setattr(mod.rules_svc, "create_rule", _boom)

    r = _client(store).post(
        "/api/v1/email/attachment-rules",
        json={"source_id": SOURCE, "match_type": "category", "match_value": "nope"},
    )

    assert r.status_code == 422
    assert "nope" in r.json()["detail"]["message"], "actionable, not merely correct"


def test_creating_a_rule_is_scoped_to_the_caller(store, monkeypatch):
    """A mallory who somehow named a mailbox is stopped by ownership; this
    asserts the caller's own id — not the body — reaches the service."""
    seen: list = []
    monkeypatch.setattr(
        mod.rules_svc,
        "create_rule",
        lambda u, s, mt, mv: seen.append(u) or {"id": "r1"},
    )

    _client(store).post(
        "/api/v1/email/attachment-rules",
        json={"source_id": SOURCE, "match_type": "sender", "match_value": "a@b.example"},
    )

    assert seen == ["u1"]


def test_deleting_a_rule_is_204(store, monkeypatch):
    monkeypatch.setattr(mod.rules_svc, "delete_rule", lambda u, r: True)

    r = _client(store).delete("/api/v1/email/attachment-rules/r1")

    assert r.status_code == 204


def test_deleting_an_unknown_or_another_users_rule_is_404_not_403(store, monkeypatch):
    monkeypatch.setattr(mod.rules_svc, "delete_rule", lambda u, r: False)

    r = _client(store).delete("/api/v1/email/attachment-rules/r1")

    assert r.status_code == 404


def test_deleting_a_rule_is_scoped_to_the_caller(store, monkeypatch):
    seen: list = []
    monkeypatch.setattr(mod.rules_svc, "delete_rule", lambda u, r: seen.append((u, r)) or True)

    _client(store, user_id="mallory").delete("/api/v1/email/attachment-rules/r1")

    assert seen == [("mallory", "r1")]
