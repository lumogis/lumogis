# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Failed-send recovery — listing, save-to-Drafts, dismiss (LUM-793).

**What this lane can and cannot prove.** The fake below pattern-matches SQL and
re-implements the filtering in Python, so it proves the *service and route
behaviour* (which rows are offered, which transitions are legal, what the route
refuses) and it does **not** prove the SQL predicates. LUM-762 demonstrated
empirically that a fake like this passes with `user_id` deleted from a WHERE
clause. Mutation A therefore lives in `tests/integration/test_recoverable_sends_live.py`,
against real Postgres, and a skip there is a blocker rather than a pass.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from datetime import timedelta
from datetime import timezone

import pytest
from fastapi.testclient import TestClient
from models.actions import ActionResult

import config
from services import action_proposals

NOW = datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc)


class RecoveryStore:
    """Proposal rows + a sent log + staged attachments, in memory."""

    def __init__(self):
        self.rows: dict[int, dict] = {}
        self.sent_log: list[dict] = []
        self.staged: list[dict] = []
        self._seq = 0

    # -- seeding ----------------------------------------------------------
    def add_dead_send(
        self,
        *,
        user_id="default",
        action_name="email_compose",
        payload=None,
        error="smtp_send_unavailable: connection reset",
        finished_at=None,
        status="dead",
    ) -> int:
        self._seq += 1
        self.rows[self._seq] = {
            "id": self._seq,
            "user_id": user_id,
            "action_name": action_name,
            "payload": payload
            or {
                "source_id": "src-1",
                "to": ["a@example.com"],
                "subject": "Dinner",
                "body": "Shall we say 7?",
            },
            "annotation": {},
            "status": status,
            "error": error,
            "created_at": NOW,
            "finished_at": NOW if finished_at is None else finished_at,
            "attempt": 1,
        }
        return self._seq

    # -- store protocol ---------------------------------------------------
    def ping(self) -> bool:
        return True

    def close(self) -> None:
        pass

    def fetch_one(self, sql, params=None):
        q = " ".join(sql.split()).lower()
        p = params or ()
        if q.startswith("update action_proposals set status = 'archived'"):
            pid, uid = int(p[0]), p[1]
            r = self.rows.get(pid)
            if r and r["user_id"] == uid and r["status"] == "dead":
                r["status"] = "archived"
                return {"id": pid, "status": "archived"}
            return None
        if "from email_sent_log" in q and "proposal_id = %s" in q:
            pid, uid = int(p[0]), p[1]
            for row in self.sent_log:
                if row.get("proposal_id") == pid and row.get("user_id") == uid:
                    return row
            return None
        if "from email_sent_log" in q:
            return None
        if q.startswith("select id, status, action_name, payload"):
            pid, uid = int(p[0]), p[1]
            r = self.rows.get(pid)
            return dict(r) if r and r["user_id"] == uid else None
        return None

    def fetch_all(self, sql, params=None):
        q = " ".join(sql.split()).lower()
        p = params or ()
        if "from action_proposals" in q and "status = 'dead'" in q:
            uid, actions, days = p[0], set(p[1]), int(p[2])
            cutoff = NOW - timedelta(days=days)
            out = [
                dict(r, payload=json.dumps(r["payload"]))
                for r in self.rows.values()
                if r["user_id"] == uid
                and r["status"] == "dead"
                and r["action_name"] in actions
                and r["finished_at"] >= cutoff
            ]
            return sorted(out, key=lambda r: r["id"], reverse=True)
        if "from email_attachment_staged" in q and "consumed_at is null" in q:
            pid, uid = int(p[0]), p[1]
            return [
                s
                for s in self.staged
                if s["proposal_id"] == pid and s["user_id"] == uid and not s.get("consumed_at")
            ]
        return []


@pytest.fixture(autouse=True)
def _auth_disabled(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    # Pin the clock the listing window is measured against.
    monkeypatch.setattr(
        "services.action_proposals._recoverable_window_days", lambda: 30, raising=True
    )


@pytest.fixture
def store():
    s = RecoveryStore()
    config._instances["metadata_store"] = s
    yield s
    config._instances.pop("metadata_store", None)


@pytest.fixture
def client(store):
    import main

    with TestClient(main.app) as c:
        yield c


# --------------------------------------------------------------------------- #
# Listing
# --------------------------------------------------------------------------- #


def test_recoverable_sends_lists_only_dead_sends(store):
    dead = store.add_dead_send()
    store.add_dead_send(status="pending")
    store.add_dead_send(status="done")
    store.add_dead_send(status="approved")

    rows = action_proposals.list_recoverable_sends("default")
    assert [r["proposal_id"] for r in rows] == [dead]


def test_recoverable_sends_includes_all_three_send_actions(store):
    ids = [
        store.add_dead_send(action_name="email_reply", payload={"signal_id": "s", "body": "x"}),
        store.add_dead_send(action_name="email_compose"),
        store.add_dead_send(
            action_name="email_forward",
            payload={"signal_id": "s", "to": ["b@example.com"], "body": ""},
        ),
    ]
    rows = action_proposals.list_recoverable_sends("default")
    assert sorted(r["proposal_id"] for r in rows) == sorted(ids)


# MUTATION K — add `email_unsubscribe` to the send set.
def test_unsubscribe_is_not_recoverable(store):
    """`email_unsubscribe` never calls `_record_sent_log`, so there is no
    evidence to resolve an outcome against — and it is not a message anyone
    wants handed back. Pinned because "we forgot" and "we decided" look
    identical in a filter."""
    store.add_dead_send(action_name="email_unsubscribe")
    assert action_proposals.list_recoverable_sends("default") == []
    assert "email_unsubscribe" not in action_proposals.RECOVERABLE_SEND_ACTIONS


def test_recoverable_sends_respects_window(store):
    recent = store.add_dead_send(finished_at=NOW - timedelta(days=29))
    store.add_dead_send(finished_at=NOW - timedelta(days=31))

    rows = action_proposals.list_recoverable_sends("default")
    assert [r["proposal_id"] for r in rows] == [recent]


def test_listing_route_renders_body_and_outcome(store, client):
    pid = store.add_dead_send()
    r = client.get("/api/v1/approvals/recoverable-sends")
    assert r.status_code == 200
    (item,) = r.json()["items"]
    assert item["proposal_id"] == pid
    assert item["body"] == "Shall we say 7?"
    assert item["subject"] == "Dinner"
    assert item["outcome"] == "not_sent"
    assert item["attachments"] == {"count": 0, "available": False, "expires_at": None}


def test_listing_route_reports_a_partial_and_names_the_refused(store, client):
    pid = store.add_dead_send(
        payload={
            "source_id": "src-1",
            "to": ["a@example.com", "b@example.com"],
            "subject": "Dinner",
            "body": "…",
        },
        error="recipients refused: b@example.com",
    )
    store.sent_log.append(
        {
            "proposal_id": pid,
            "user_id": "default",
            "to_addrs": ["a@example.com"],
            "cc_addrs": [],
            "bcc_addrs": [],
            "sent_at": NOW,
        }
    )
    (item,) = client.get("/api/v1/approvals/recoverable-sends").json()["items"]
    assert item["outcome"] == "partially_sent"
    assert item["delivered_to"] == ["a@example.com"]
    assert item["refused"] == ["b@example.com"]


# MUTATION F — read attachment availability from the payload.
def test_attachment_availability_reads_staging_not_payload(store, client):
    pid = store.add_dead_send(
        payload={
            "source_id": "src-1",
            "to": ["a@example.com"],
            "subject": "Dinner",
            "body": "…",
            "attachment_ids": ["s1", "s2", "s3"],
        }
    )
    store.staged.append(
        {
            "id": "s1",
            "user_id": "default",
            "proposal_id": pid,
            "filename": "menu.pdf",
            "mime_type": "application/pdf",
            "size_bytes": 10,
            "consumed_at": None,
        }
    )
    (item,) = client.get("/api/v1/approvals/recoverable-sends").json()["items"]
    # The payload says three; only one file is actually still on disk.
    assert item["attachments"]["count"] == 1
    assert item["attachments"]["available"] is True
    assert item["attachments"]["expires_at"] is not None


# --------------------------------------------------------------------------- #
# Save to Drafts
# --------------------------------------------------------------------------- #


@pytest.fixture
def saved(monkeypatch):
    """Capture the input `email_save_draft` is actually run with."""
    calls: list[dict] = []

    def _fake_execute(action_name, payload, **kwargs):
        calls.append({"action_name": action_name, "payload": payload, **kwargs})
        return ActionResult(success=True, output="imap:draft")

    monkeypatch.setattr("routes.api_v1.approvals.execute", _fake_execute)
    return calls


def test_save_draft_translates_compose_payload(store, client, saved):
    pid = store.add_dead_send()
    r = client.post(f"/api/v1/approvals/recoverable-sends/{pid}/save-draft")
    assert r.status_code == 200
    assert r.json()["success"] is True
    assert saved[0]["action_name"] == "email_save_draft"
    assert saved[0]["payload"]["source_id"] == "src-1"
    assert saved[0]["payload"]["body"] == "Shall we say 7?"
    # Identity is the authenticated caller, and the human already approved.
    assert saved[0]["user_id"] == "default"
    assert saved[0]["approved"] is True


def test_save_draft_translates_reply_payload(store, client, saved):
    pid = store.add_dead_send(
        action_name="email_reply", payload={"signal_id": "sig-1", "body": "sure"}
    )
    assert client.post(f"/api/v1/approvals/recoverable-sends/{pid}/save-draft").status_code == 200
    assert saved[0]["payload"] == {"signal_id": "sig-1", "body": "sure"}


def test_save_draft_translates_forward_payload(store, client, saved):
    pid = store.add_dead_send(
        action_name="email_forward",
        payload={
            "signal_id": "sig-1",
            "to": ["b@example.com"],
            "body": "fyi",
            "forward_part_indexes": [1, 2],
        },
    )
    assert client.post(f"/api/v1/approvals/recoverable-sends/{pid}/save-draft").status_code == 200
    # Forward part indexes are re-fetched from the signal and never expire, so
    # they survive translation untouched.
    assert saved[0]["payload"]["forward_part_indexes"] == [1, 2]


# MUTATION D′ — drop the shape validation before `email_save_draft`.
def test_save_draft_rejects_malformed_shape(store, client, saved):
    """A payload satisfying neither draft shape is a loud 422, never a partial
    draft or a dead-lettered proposal at execute time."""
    pid = store.add_dead_send(payload={"source_id": "src-1", "to": [], "subject": "", "body": "x"})
    r = client.post(f"/api/v1/approvals/recoverable-sends/{pid}/save-draft")
    assert r.status_code == 422
    assert r.json()["detail"]["error"] == "unsaveable_payload"
    assert saved == []


def test_save_draft_shape_rejection_never_logs_the_message_body(store, client, saved, caplog):
    """`str(ValidationError)` embeds `input_value=...`.

    Logging the exception verbatim writes a household member's private message
    body and recipient addresses into the orchestrator log — somewhere with
    different read permissions than the row it came from, in a feature whose
    whole point is that these bodies are owner-scoped. The field paths and error
    types are what a diagnosis needs; the values are not.
    """
    # A SHORT payload on purpose. Pydantic elides the middle of `input_value`
    # at ~50 chars, so a long body leaks only its head and tail — which is why
    # the first version of this test passed against the leaking code and proved
    # nothing. A short message is fully inside the window, and a short message
    # is exactly the kind that carries the most per-character.
    secret_body = "Divorce papers ready"
    pid = store.add_dead_send(
        payload={"source_id": "s", "to": [], "subject": "", "body": secret_body}
    )
    with caplog.at_level(logging.WARNING):
        r = client.post(f"/api/v1/approvals/recoverable-sends/{pid}/save-draft")

    assert r.status_code == 422
    logged = "\n".join(rec.getMessage() for rec in caplog.records)
    assert secret_body not in logged, "the message body reached the log"
    # Still diagnosable: the failing field and its error type survive.
    assert "unsaveable" in r.json()["detail"]["error"]
    assert str(pid) in logged
    # And the body must not reach the client either.
    assert secret_body not in r.text


def test_save_draft_drops_expired_attachments_and_reports_them(store, client, saved):
    """A draft is NOT a send: refusing to save because a file expired leaves the
    user with nothing, which is strictly worse than a draft plus a sentence."""
    pid = store.add_dead_send(
        payload={
            "source_id": "src-1",
            "to": ["a@example.com"],
            "subject": "Dinner",
            "body": "…",
            "attachment_ids": ["s1", "s2"],
        }
    )
    store.staged.append(
        {
            "id": "s1",
            "user_id": "default",
            "proposal_id": pid,
            "filename": "menu.pdf",
            "mime_type": "application/pdf",
            "size_bytes": 10,
            "consumed_at": None,
        }
    )
    r = client.post(f"/api/v1/approvals/recoverable-sends/{pid}/save-draft")
    assert r.status_code == 200
    assert r.json()["success"] is True
    assert r.json()["attachments_dropped"] == 1
    assert saved[0]["payload"]["attachment_ids"] == ["s1"]


# MUTATION G — drop the server-side `sent` re-check.
def test_save_draft_refuses_when_outcome_is_sent(store, client, saved):
    """The listing is a page a household member leaves open for hours. Hiding
    the button client-side is not a guard on a server-side side effect."""
    pid = store.add_dead_send()
    store.sent_log.append(
        {
            "proposal_id": pid,
            "user_id": "default",
            "to_addrs": ["a@example.com"],
            "cc_addrs": [],
            "bcc_addrs": [],
            "sent_at": NOW,
        }
    )
    r = client.post(f"/api/v1/approvals/recoverable-sends/{pid}/save-draft")
    assert r.status_code == 409
    assert r.json()["detail"]["error"] == "already_sent"
    assert saved == [], "no draft may be written for a message that already went out"


def test_save_draft_still_offered_for_a_partial_send(store, client, saved):
    """The 409 keys on `sent` ONLY — a partial is exactly the case that still
    needs recovering, for the recipients who never received it."""
    pid = store.add_dead_send(
        payload={
            "source_id": "src-1",
            "to": ["a@example.com", "b@example.com"],
            "subject": "Dinner",
            "body": "…",
        },
        error="recipients refused: b@example.com",
    )
    store.sent_log.append(
        {
            "proposal_id": pid,
            "user_id": "default",
            "to_addrs": ["a@example.com"],
            "cc_addrs": [],
            "bcc_addrs": [],
            "sent_at": NOW,
        }
    )
    r = client.post(f"/api/v1/approvals/recoverable-sends/{pid}/save-draft")
    assert r.status_code == 200
    assert len(saved) == 1


def test_failed_save_draft_leaves_row_listed(store, client, monkeypatch):
    """Being able to try again is the entire feature."""
    monkeypatch.setattr(
        "routes.api_v1.approvals.execute",
        lambda *a, **k: ActionResult(
            success=False, output="", error="no Drafts folder", code="no_drafts_folder"
        ),
    )
    pid = store.add_dead_send()
    r = client.post(f"/api/v1/approvals/recoverable-sends/{pid}/save-draft")

    assert r.status_code == 200
    assert r.json()["success"] is False
    assert r.json()["code"] == "no_drafts_folder"
    assert store.rows[pid]["status"] == "dead"
    listed = client.get("/api/v1/approvals/recoverable-sends").json()["items"]
    assert [i["proposal_id"] for i in listed] == [pid]


def test_save_draft_on_unsubscribe_row_is_404(store, client, saved):
    """The write path enforces the same action set as the listing — a row that
    cannot be listed cannot be saved either."""
    pid = store.add_dead_send(action_name="email_unsubscribe")
    assert client.post(f"/api/v1/approvals/recoverable-sends/{pid}/save-draft").status_code == 404
    assert saved == []


# --------------------------------------------------------------------------- #
# Dismiss — the new `dead → archived` edge
# --------------------------------------------------------------------------- #


def test_dismiss_moves_dead_to_archived(store, client):
    pid = store.add_dead_send()
    r = client.post(f"/api/v1/approvals/recoverable-sends/{pid}/dismiss")
    assert r.status_code == 200
    assert r.json()["status"] == "archived"
    assert store.rows[pid]["status"] == "archived"
    # Deletes nothing — the body survives for audit.
    assert store.rows[pid]["payload"]["body"] == "Shall we say 7?"


def test_dismissed_row_leaves_listing(store, client):
    pid = store.add_dead_send()
    client.post(f"/api/v1/approvals/recoverable-sends/{pid}/dismiss")
    assert client.get("/api/v1/approvals/recoverable-sends").json()["items"] == []


# MUTATION H — make `dismiss` write `approved` instead of `archived`.
def test_dismiss_never_produces_approved(store, client):
    """`dead → approved` is the ONE transition ADR-201 forbids: it would make a
    failed send re-executable and re-open the duplicate-email hazard LUM-762
    closed. This is the mutation an over-eager "make it recoverable again"
    refactor introduces."""
    pid = store.add_dead_send()
    client.post(f"/api/v1/approvals/recoverable-sends/{pid}/dismiss")
    assert store.rows[pid]["status"] != "approved"
    assert store.rows[pid]["status"] in {"dead", "archived"}


def test_dismiss_is_not_repeatable(store, client):
    pid = store.add_dead_send()
    assert client.post(f"/api/v1/approvals/recoverable-sends/{pid}/dismiss").status_code == 200
    assert client.post(f"/api/v1/approvals/recoverable-sends/{pid}/dismiss").status_code == 404


# --------------------------------------------------------------------------- #
# Cross-user (behavioural — the SQL-level proof is in the Postgres lane)
# --------------------------------------------------------------------------- #


def test_save_draft_cross_user_404(store, client, saved):
    pid = store.add_dead_send(user_id="alice")
    assert client.post(f"/api/v1/approvals/recoverable-sends/{pid}/save-draft").status_code == 404
    assert saved == []


def test_dismiss_cross_user_404(store, client):
    pid = store.add_dead_send(user_id="alice")
    assert client.post(f"/api/v1/approvals/recoverable-sends/{pid}/dismiss").status_code == 404
    assert store.rows[pid]["status"] == "dead"


def test_listing_omits_another_users_rows(store, client):
    store.add_dead_send(user_id="alice")
    mine = store.add_dead_send(user_id="default")
    items = client.get("/api/v1/approvals/recoverable-sends").json()["items"]
    assert [i["proposal_id"] for i in items] == [mine]
