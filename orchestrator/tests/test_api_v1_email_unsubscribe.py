# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Route-level tests for the unsubscribe surface (LUM-697).

The security-relevant ones: a non-owned mailbox is a 404 (never a 403, which
would confirm the source exists), the propose route resolves recipients
server-side so no address crosses the boundary inbound, and the sender-rules
limiter is its own bucket rather than sharing the LLM budget.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from routes.api_v1 import email_unsubscribe as route_mod

import config
from services import email_sender_rules

_UID = "user-A"
_SID = "11111111-1111-1111-1111-111111111111"


class _Store:
    def __init__(self):
        self.targets = {
            "news@x.com": {"mailto_addr": "u@x.com", "mailto_subject": "unsubscribe"},
            "nomailto@x.com": {"mailto_addr": "", "mailto_subject": ""},
        }
        self.rules: list[dict] = []
        # LUM-710: one queued unsubscribe batch, owned by _UID.
        self.jobs: list[dict] = [
            {"id": 7, "status": "running", "enqueued_at": None, "finished_at": None, "total": 2}
        ]
        self.job_row: dict | None = {
            "id": 7,
            "user_id": _UID,
            "kind": "email_unsubscribe",
            "status": "running",
            "attempt": 0,
            "payload": {
                "source_id": _SID,
                "targets": [
                    {"from_addr": "news@x.com", "mailto_addr": "u@x.com", "subject": "u"},
                    {"from_addr": "other@x.com", "mailto_addr": "v@x.com", "subject": "u"},
                ],
            },
            "enqueued_at": None,
            "started_at": None,
            "finished_at": None,
        }

    def fetch_one(self, q, params=None):
        ql = " ".join((q or "").lower().split())
        if "select mailto_addr, mailto_subject from email_unsubscribe_target" in ql:
            return self.targets.get((params or ("", "", ""))[2])
        if "from user_batch_jobs where id" in ql:
            row = self.job_row
            if row is None or int(params[0]) != row["id"]:
                return None
            # Honour a WHERE predicate only when the query states it — a fake
            # that filters in Python would pass even after the SQL stopped
            # scoping, certifying a guard that isn't there.
            i = 1
            for col in ("user_id", "kind"):
                if f"{col} = %s" in ql:
                    if row[col] != params[i]:
                        return None
                    i += 1
            return dict(row)
        return None

    def fetch_all(self, q, params=None):
        ql = " ".join((q or "").lower().split())
        p = params or ()
        if "from email_unsubscribe_target t" in ql:
            return [
                {
                    "source_id": _SID,
                    "from_addr": "news@x.com",
                    "from_display": "News",
                    "message_count": 41,
                    "has_mailto": True,
                    "has_https": True,
                    "one_click": True,
                    "https_uri": "https://x.com/u",
                    "last_seen_at": None,
                    "unsubscribed_at": None,
                    "suppressed_as": None,
                },
            ]
        if "from email_sender_rule where user_id = %s and active" in ql:
            return [r for r in self.rules if r["active"]]
        if "select source_id::text as source_id, from_addr, action, active, created_at" in ql:
            return list(self.rules)
        if "from user_batch_jobs where user_id" in ql:
            return list(self.jobs)
        if "from email_unsubscribe_target" in ql and "= any(%s)" in ql:
            return [{"from_addr": "news@x.com"}]
        if "delete from email_sender_rule" in ql:
            before = len(self.rules)
            self.rules = [r for r in self.rules if r["from_addr"] != p[2]]
            return [{"id": 1}] * (before - len(self.rules))
        return []

    def execute(self, q, params=None):
        ql = " ".join((q or "").lower().split())
        if "insert into email_sender_rule" in ql:
            p = params or ()
            self.rules.append(
                {
                    "source_id": p[1],
                    "from_addr": p[2],
                    "action": p[3],
                    "active": True,
                    "created_at": None,
                    "user_id": p[0],
                }
            )


@pytest.fixture
def client(monkeypatch):
    store = _Store()
    monkeypatch.setattr(config, "get_metadata_store", lambda: store)
    email_sender_rules.invalidate_cache()
    monkeypatch.setattr(route_mod, "_require_owned", lambda sid, uid: None)
    monkeypatch.setattr(
        "services.action_proposals.create_proposal",
        lambda action_name, payload, *, user_id: 4242,
    )
    for q in list(route_mod._rules_calls.values()):
        q.clear()

    from main import app

    class _U:
        user_id = _UID
        role = "user"

    monkeypatch.setattr(route_mod, "get_user", lambda request: _U())
    app.dependency_overrides = getattr(app, "dependency_overrides", {})
    from authz import require_user

    app.dependency_overrides[require_user] = lambda: _U()
    yield TestClient(app), store
    app.dependency_overrides.pop(require_user, None)
    email_sender_rules.invalidate_cache()


# --------------------------------------------------------------------------- #
# subscriptions list
# --------------------------------------------------------------------------- #
def test_lists_subscriptions_with_the_https_uri_for_the_browser(client):
    c, _ = client
    r = c.get(f"/api/v1/email/subscriptions?source_id={_SID}")
    assert r.status_code == 200
    row = r.json()["rows"][0]
    assert row["from_addr"] == "news@x.com"
    assert row["message_count"] == 41
    # Returned so the BROWSER can follow it — nothing server-side fetches it.
    assert row["https_uri"] == "https://x.com/u"


def test_subscriptions_404_for_a_non_owned_mailbox(client, monkeypatch):
    from fastapi import HTTPException

    c, _ = client

    def _deny(sid, uid):
        raise HTTPException(status_code=404, detail="not_found")

    monkeypatch.setattr(route_mod, "_require_owned", _deny)
    r = c.get(f"/api/v1/email/subscriptions?source_id={_SID}")
    assert r.status_code == 404, "must be 404, not 403 — a 403 confirms the source exists"


def test_subscriptions_limit_is_capped_at_validation(client):
    c, _ = client
    assert c.get("/api/v1/email/subscriptions?limit=5000").status_code == 422


# --------------------------------------------------------------------------- #
# propose — server-side resolution
# --------------------------------------------------------------------------- #
def test_propose_resolves_recipients_server_side(client):
    c, _ = client
    r = c.post(
        "/api/v1/email/unsubscribe/propose",
        json={"source_id": _SID, "senders": ["news@x.com"]},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["proposal_id"] == 4242
    assert body["targets"] == [
        {"from_addr": "news@x.com", "mailto_addr": "u@x.com", "subject": "unsubscribe"}
    ]


def test_propose_reports_senders_with_no_mailto_as_skipped(client):
    c, _ = client
    r = c.post(
        "/api/v1/email/unsubscribe/propose",
        json={"source_id": _SID, "senders": ["news@x.com", "nomailto@x.com"]},
    )
    assert r.json()["skipped"] == ["nomailto@x.com"]


def test_propose_422_when_nothing_resolves(client):
    c, _ = client
    r = c.post(
        "/api/v1/email/unsubscribe/propose",
        json={"source_id": _SID, "senders": ["nomailto@x.com"]},
    )
    assert r.status_code == 422


def test_propose_rejects_a_client_supplied_recipient(client):
    """The recipient must never cross the boundary inbound."""
    c, _ = client
    r = c.post(
        "/api/v1/email/unsubscribe/propose",
        json={"source_id": _SID, "senders": ["news@x.com"], "mailto_addr": "evil@x.com"},
    )
    assert r.status_code == 422


def test_propose_batch_capped(client):
    """Derived from the action's cap, and distinguished from the *other* 422.

    A too-large batch and a batch that resolves to nothing both return 422, so a
    literal `range(11)` kept passing after LUM-710 raised the cap to 50 — on the
    no-mailto path, testing nothing. `detail` separates them.
    """
    from actions.handlers.smtp_send import MAX_UNSUBSCRIBE_BATCH

    c, _ = client
    over = MAX_UNSUBSCRIBE_BATCH + 1
    r = c.post(
        "/api/v1/email/unsubscribe/propose",
        json={"source_id": _SID, "senders": [f"a{i}@x.com" for i in range(over)]},
    )
    assert r.status_code == 422
    assert r.json().get("detail") != "no_mailto_targets", "rejected for the wrong reason"

    at_cap = c.post(
        "/api/v1/email/unsubscribe/propose",
        json={"source_id": _SID, "senders": [f"a{i}@x.com" for i in range(MAX_UNSUBSCRIBE_BATCH)]},
    )
    # Exactly at the cap clears validation and fails later, on resolution — which
    # is what proves `over` was rejected by the cap and not by the list length.
    assert at_cap.json().get("detail") == "no_mailto_targets"


# --------------------------------------------------------------------------- #
# sender rules
# --------------------------------------------------------------------------- #
def test_set_and_list_a_hide_rule(client):
    c, _ = client
    assert (
        c.post(
            "/api/v1/email/sender-rules",
            json={"source_id": _SID, "from_addr": "news@x.com", "action": "hide"},
        ).status_code
        == 200
    )
    rows = c.get(f"/api/v1/email/sender-rules?source_id={_SID}").json()["rules"]
    assert rows[0]["from_addr"] == "news@x.com"
    assert rows[0]["action"] == "hide"


def test_rejects_a_non_hide_action(client):
    c, _ = client
    for bad in ("mark_read", "trash", "delete"):
        r = c.post(
            "/api/v1/email/sender-rules",
            json={"source_id": _SID, "from_addr": "a@x.com", "action": bad},
        )
        assert r.status_code == 422, f"{bad} is deferred (LUM-709), not silently accepted"


def test_clear_rule_uses_query_params_and_404s_when_absent(client):
    c, _ = client
    c.post("/api/v1/email/sender-rules", json={"source_id": _SID, "from_addr": "news@x.com"})
    assert (
        c.request(
            "DELETE", f"/api/v1/email/sender-rules?source_id={_SID}&from_addr=news@x.com"
        ).status_code
        == 200
    )
    assert (
        c.request(
            "DELETE", f"/api/v1/email/sender-rules?source_id={_SID}&from_addr=news@x.com"
        ).status_code
        == 404
    )


def test_sender_rules_rate_limited_on_its_own_bucket(client):
    c, _ = client
    codes = [
        c.post(
            "/api/v1/email/sender-rules",
            json={"source_id": _SID, "from_addr": f"a{i}@x.com"},
        ).status_code
        for i in range(route_mod._RULES_LIMIT + 2)
    ]
    assert 429 in codes


def test_rules_bucket_is_isolated_from_the_draft_bucket(client):
    """Draining the rules limiter must leave the LLM-budget buckets untouched."""
    from routes.api_v1 import email_send

    c, _ = client
    for i in range(route_mod._RULES_LIMIT + 2):
        c.post("/api/v1/email/sender-rules", json={"source_id": _SID, "from_addr": f"b{i}@x.com"})
    assert all(len(q) == 0 for q in email_send._draft_calls.values())


# --------------------------------------------------------------------------- #
# batch progress (LUM-710)
# --------------------------------------------------------------------------- #
def test_progress_route_reports_each_target_separately(client):
    c, _ = client
    r = c.get("/api/v1/email/unsubscribe/jobs/7")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "running"
    assert body["sent"] == 1 and body["outstanding"] == 1
    assert body["targets"] == [
        {"from_addr": "news@x.com", "unsubscribed": True},
        {"from_addr": "other@x.com", "unsubscribed": False},
    ]


def test_progress_route_404s_for_a_job_that_is_not_yours(client):
    """404, never 403 — a 403 would confirm the id exists, which is enough to
    walk the sequence and count another member's unsubscribe activity."""
    c, store = client
    store.job_row = None
    assert c.get("/api/v1/email/unsubscribe/jobs/7").status_code == 404
    assert c.get("/api/v1/email/unsubscribe/jobs/999").status_code == 404


def test_jobs_list_is_how_a_client_finds_the_batch_it_just_approved(client):
    c, _ = client
    r = c.get("/api/v1/email/unsubscribe/jobs")
    assert r.status_code == 200
    assert [j["job_id"] for j in r.json()["jobs"]] == [7]


def test_the_progress_route_never_returns_the_queues_error_text(client):
    """`user_batch_jobs.error` is repr() of whatever SMTP raised, on a path where
    the remote end is the sender being unsubscribed from."""
    c, _ = client
    assert "error" not in c.get("/api/v1/email/unsubscribe/jobs/7").json()
