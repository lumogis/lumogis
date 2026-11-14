# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""LUM-646 — email_cleanup service logic with an in-memory fake store.

Covers the route-facing guards (single-active-job 409, approve state gate, undo enqueue,
summary grouping) and the correctness-critical tick behaviours that don't need real SQL:
the spam walk targets the \\Junk folder with op="trash", and job-scoped undo reverses ONLY
this job's ledger rows (never a standing-organize row). The real per-op LEFT JOIN / GROUP BY
against Postgres are proven in tests/integration/test_email_cleanup_reconcile.py.
"""

from __future__ import annotations

import json

import pytest

from services import email_cleanup as ec
from services import email_organize as eo

_SID = "11111111-1111-1111-1111-111111111111"


class _FakeStore:
    def __init__(self):
        self.jobs: dict[str, dict] = {}
        self.items: list[dict] = []
        self.ledger: list[dict] = []
        self._nid = 0
        self.sources = {"url": "imap://imap.gmail.com:993/p", "organize_label_enabled": False}

    # -- fetch_one -----------------------------------------------------------
    def fetch_one(self, q, params=None):
        p = params or ()
        if q.startswith("INSERT INTO email_cleanup_job"):
            self._nid += 1
            jid = f"job-{self._nid}"
            kind = "spam" if "'spam'" in q else "window"
            job = {
                "id": jid,
                "user_id": p[0],
                "source_id": p[1],
                "kind": kind,
                "state": "backfilling",
                "backfill_cursor": 0,
                "backfill_max_uid": p[-2],
                "backfill_uidvalidity": p[-1],
                "window_start": None,
                "window_end": None,
                "approved_actions": [],
                "undo_ops": None,
                "undo_ticks": 0,
                "scanned": 0,
                "classified": 0,
                "labeled": 0,
                "executed": 0,
                "reversed": 0,
            }
            if kind == "window":
                job["window_start"], job["window_end"] = p[2], p[3]
            self.jobs[jid] = job
            return {"id": jid}
        if "FROM email_cleanup_job WHERE source_id" in q and "state = ANY" in q:
            active = [
                j for j in self.jobs.values() if j["source_id"] == p[0] and j["state"] in p[1]
            ]
            return dict(active[-1]) if active else None
        if "FROM email_cleanup_job WHERE id" in q:
            j = self.jobs.get(p[0])
            if j and j["user_id"] == p[1] and j["source_id"] == p[2]:
                return dict(j)
            return None
        if "organize_label_enabled FROM sources" in q:
            return {"organize_label_enabled": self.sources["organize_label_enabled"]}
        if "SELECT url FROM sources" in q:
            return {"url": self.sources["url"]}
        if "SELECT 1 FROM signals" in q:
            return None  # never a duplicate in these tests
        if "SELECT 1 FROM email_organize_log" in q and "cleanup_job_id" in q:
            jid, ops = p[0], p[1]
            live = [
                r
                for r in self.ledger
                if r["cleanup_job_id"] == jid and r["reversed_at"] is None and r["op"] in ops
            ]
            return {"1": 1} if live else None
        if "FROM email_organize_log" in q and "reversed_at IS NULL LIMIT 1" in q:
            url, op = p[0], p[1]
            live = [
                r
                for r in self.ledger
                if r["signal_url"] == url and r["op"] == op and r["reversed_at"] is None
            ]
            return {"1": 1} if live else None
        return None

    # -- fetch_all -----------------------------------------------------------
    def fetch_all(self, q, params=None):
        p = params or ()
        if "COUNT(*) AS n FROM email_cleanup_item" in q:
            jid = p[0]
            counts: dict[str, int] = {}
            for it in self.items:
                if it["job_id"] == jid and it["category"]:
                    counts[it["category"]] = counts.get(it["category"], 0) + 1
            return [{"category": c, "n": n} for c, n in sorted(counts.items())]
        if "SELECT url, category FROM signals" in q:
            return []  # membership read (window backfill) — empty in these unit tests
        if "SELECT id, signal_url, op, reverse_token FROM email_organize_log" in q:
            jid, ops = p[0], p[1]
            rows = [
                r
                for r in self.ledger
                if r["cleanup_job_id"] == jid and r["reversed_at"] is None and r["op"] in ops
            ]
            return sorted(rows, key=lambda r: (r["op"] not in ("move", "trash"), r["id"]))
        if q.startswith("UPDATE email_cleanup_job SET state = 'failed'") and "RETURNING id" in q:
            source_id, user_id, states = p[0], p[1], p[2]
            hit = [
                j
                for j in self.jobs.values()
                if j["source_id"] == source_id and j["user_id"] == user_id and j["state"] in states
            ]
            for j in hit:
                j["state"] = "failed"
            return [{"id": j["id"]} for j in hit]
        return []

    # -- execute -------------------------------------------------------------
    def execute(self, q, params=None):
        p = params or ()
        if q.startswith("UPDATE email_cleanup_job"):
            jid = p[-1]
            job = self.jobs.get(jid)
            if not job:
                return
            if "state = %s" in q:
                job["state"] = p[0]
            if "approved_actions = %s::jsonb" in q:
                job["approved_actions"] = json.loads(p[0])
                job["state"] = "executing"
            if "undo_ops = %s" in q and "state = 'undoing'" in q:
                job["undo_ops"] = p[0]
                job["state"] = "undoing"
            if "state = 'done'" in q:
                job["state"] = "done"
            if "state = 'failed'" in q:
                job["state"] = "failed"
            if "undo_ops = NULL" in q and "state" not in q:
                job["undo_ops"] = None
            if "backfill_cursor = %s" in q:
                job["backfill_cursor"] = p[0]
                if "backfill_uidvalidity = %s" in q:
                    job["backfill_uidvalidity"] = p[1]
                if "executed = executed + %s" in q:
                    job["executed"] = job.get("executed", 0) + p[3]
        elif "INSERT INTO email_cleanup_item" in q:
            cat = p[2] if len(p) > 2 else "__spam__"  # spam INSERT hard-codes '__spam__'
            self.items.append({"job_id": p[0], "signal_url": p[1], "category": cat})
        elif "INSERT INTO email_organize_log" in q:
            self._nid += 1
            self.ledger.append(
                {
                    "id": self._nid,
                    "signal_url": p[2],
                    "op": p[3],
                    "target": p[4],
                    "reverse_token": json.loads(p[6]) if isinstance(p[6], str) else p[6],
                    "cleanup_job_id": p[7],
                    "reversed_at": None,
                }
            )
        elif "UPDATE email_organize_log SET reversed_at" in q:
            for r in self.ledger:
                if r["id"] == p[0]:
                    r["reversed_at"] = "now"


class _RecordingAdapter:
    def __init__(self, *, junk="[Gmail]/Spam", trash="[Gmail]/Trash", uids=(1, 2, 3), fail_uids=()):
        self.junk, self.trash, self.uids = junk, trash, list(uids)
        self.fail_uids = frozenset(fail_uids)
        self.applied: list = []
        self.reversed: list = []

    def organize_session(self):
        return object()

    def close_session(self, client):
        pass

    def _role_folder(self, client, role):
        return {"junk": self.junk, "trash": self.trash}.get(role)

    def folder_uidnext(self, client, folder):
        return 100, 50

    def enumerate_role_folder(self, client, *, role, after_uid, max_uid, limit=200):
        if self._role_folder(client, role) is None:
            return None, 0, [], after_uid, True
        if not self.uids:
            return self.junk, 100, [], after_uid, True
        return self.junk, 100, list(self.uids), max(self.uids), False

    def apply_organize(self, *, uidvalidity, uid, op, label, slug="", mailbox="INBOX", client=None):
        if uid in self.fail_uids:
            raise RuntimeError("simulated trash failure")
        self.applied.append({"op": op, "uid": uid, "mailbox": mailbox})
        target = self.trash if op == "trash" else f"L/{label}"
        return {
            "op": "move" if op == "trash" else op,
            "mailbox": mailbox,
            "message_id": f"<mid-{uid}>",
            "target": target,
            "label_kind": None,
        }

    def reverse_writeback(self, *, op, mailbox, message_id, target=None, flag=None, client=None):
        self.reversed.append({"op": op, "mailbox": mailbox, "message_id": message_id})


@pytest.fixture
def store(monkeypatch):
    s = _FakeStore()
    monkeypatch.setattr(ec.config, "get_metadata_store", lambda: s)
    monkeypatch.setattr(eo.config, "get_metadata_store", lambda: s)
    return s


class _Src:
    id = _SID
    user_id = "u"
    url = "imap://imap.gmail.com:993/p"


# --------------------------------------------------------------------------- #
# Route-facing guards
# --------------------------------------------------------------------------- #
def test_start_job_rejects_when_active(store, monkeypatch):
    monkeypatch.setattr(ec, "_seed_folder", lambda sid, uid, role: ("INBOX", 100, 42))
    ec.start_job(_SID, "u", None, None)
    with pytest.raises(ec.CleanupError) as exc:
        ec.start_job(_SID, "u", None, None)
    assert exc.value.code == "cleanup_active"


def test_cancel_active_jobs_for_source_fails_active_job(store, monkeypatch):
    """LUM-701: mailbox delete forces a non-terminal job to 'failed' so the
    poll-tick job runner never ticks it forever against a source that's gone."""
    monkeypatch.setattr(ec, "_seed_folder", lambda sid, uid, role: ("INBOX", 100, 42))
    job_id = ec.start_job(_SID, "u", None, None)["job_id"]
    assert store.jobs[job_id]["state"] == "backfilling"

    n = ec.cancel_active_jobs_for_source(_SID, "u")

    assert n == 1
    assert store.jobs[job_id]["state"] == "failed"


def test_cancel_active_jobs_for_source_ignores_terminal_jobs(store, monkeypatch):
    monkeypatch.setattr(ec, "_seed_folder", lambda sid, uid, role: ("INBOX", 100, 42))
    job_id = ec.start_job(_SID, "u", None, None)["job_id"]
    store.jobs[job_id]["state"] = "done"

    n = ec.cancel_active_jobs_for_source(_SID, "u")

    assert n == 0
    assert store.jobs[job_id]["state"] == "done"


def test_cancel_active_jobs_for_source_scoped_to_owner(store, monkeypatch):
    monkeypatch.setattr(ec, "_seed_folder", lambda sid, uid, role: ("INBOX", 100, 42))
    job_id = ec.start_job(_SID, "u", None, None)["job_id"]

    n = ec.cancel_active_jobs_for_source(_SID, "someone-else")

    assert n == 0
    assert store.jobs[job_id]["state"] == "backfilling"


def test_start_spam_job_no_junk_folder_409(store, monkeypatch):
    monkeypatch.setattr(ec, "_seed_folder", lambda sid, uid, role: (None, 0, 0))
    with pytest.raises(ec.CleanupError) as exc:
        ec.start_spam_job(_SID, "u")
    assert exc.value.code == "no_spam_folder"


def test_window_and_spam_mutually_exclusive(store, monkeypatch):
    monkeypatch.setattr(
        ec, "_seed_folder", lambda sid, uid, role: (None, 0, 42) if role is None else ("J", 100, 9)
    )
    ec.start_job(_SID, "u", None, None)
    with pytest.raises(ec.CleanupError) as exc:
        ec.start_spam_job(_SID, "u")
    assert exc.value.code == "cleanup_active"


def test_approve_requires_ready(store, monkeypatch):
    monkeypatch.setattr(ec, "_seed_folder", lambda sid, uid, role: ("INBOX", 100, 42))
    jid = ec.start_job(_SID, "u", None, None)["job_id"]  # state=backfilling
    with pytest.raises(ec.CleanupError) as exc:
        ec.approve(jid, "u", _SID, [{"category": "promotional_bulk", "move": True}])
    assert exc.value.code == "job_not_ready"


def test_approve_filters_and_sets_executing(store, monkeypatch):
    monkeypatch.setattr(ec, "_seed_folder", lambda sid, uid, role: ("INBOX", 100, 42))
    jid = ec.start_job(_SID, "u", None, None)["job_id"]
    store.jobs[jid]["state"] = "ready"
    out = ec.approve(
        jid,
        "u",
        _SID,
        [
            {"category": "promotional_bulk", "move": True},
            {"category": "other", "move": True},  # dropped (protected)
            {"category": "bills_finance", "move": False},  # dropped (no move)
        ],
    )
    assert out["approved"] == 1
    assert store.jobs[jid]["state"] == "executing"
    assert store.jobs[jid]["approved_actions"] == [{"category": "promotional_bulk", "move": True}]


def test_owned_job_cross_user_is_404(store, monkeypatch):
    monkeypatch.setattr(ec, "_seed_folder", lambda sid, uid, role: ("INBOX", 100, 42))
    jid = ec.start_job(_SID, "u", None, None)["job_id"]
    with pytest.raises(ec.CleanupError) as exc:
        ec.job_summary(jid, "someone_else", _SID)
    assert exc.value.code == "not_found"


# --------------------------------------------------------------------------- #
# Tick behaviours
# --------------------------------------------------------------------------- #
def test_spam_walk_trashes_junk_with_job_attribution(store):
    adapter = _RecordingAdapter(uids=(1, 2))
    store.jobs["job-x"] = {
        "id": "job-x",
        "user_id": "u",
        "source_id": _SID,
        "kind": "spam",
        "state": "backfilling",
        "backfill_cursor": 0,
        "backfill_max_uid": 100,
        "backfill_uidvalidity": None,
        "scanned": 0,
        "executed": 0,
    }
    import services.email_cleanup as m

    orig = m.eo._adapter_for
    m.eo._adapter_for = lambda sid, uid: adapter
    try:
        m._run_spam_walk(store, _Src(), dict(store.jobs["job-x"]))
    finally:
        m.eo._adapter_for = orig
    # Every applied op is trash, sourced from the \Junk folder, attributed to the job.
    assert [a["op"] for a in adapter.applied] == ["trash", "trash"]
    assert all(a["mailbox"] == "[Gmail]/Spam" for a in adapter.applied)
    assert all(r["cleanup_job_id"] == "job-x" for r in store.ledger)
    # Membership recorded as __spam__.
    assert {it["category"] for it in store.items} == {"__spam__"}


def test_spam_walk_cursor_stops_on_trash_failure(store):
    """Failed trash ops must not advance the walk cursor past the failure (LUM-646)."""
    adapter = _RecordingAdapter(uids=(1, 2, 3), fail_uids=(2,))
    store.jobs["job-x"] = {
        "id": "job-x",
        "user_id": "u",
        "source_id": _SID,
        "kind": "spam",
        "state": "backfilling",
        "backfill_cursor": 0,
        "backfill_max_uid": 100,
        "backfill_uidvalidity": None,
        "scanned": 0,
        "executed": 0,
    }
    import services.email_cleanup as m

    orig = m.eo._adapter_for
    m.eo._adapter_for = lambda sid, uid: adapter
    try:
        m._run_spam_walk(store, _Src(), dict(store.jobs["job-x"]))
    finally:
        m.eo._adapter_for = orig
    assert [a["uid"] for a in adapter.applied] == [1]
    assert store.jobs["job-x"]["backfill_cursor"] == 1
    assert store.jobs["job-x"]["executed"] == 1


def test_job_scoped_undo_reverses_only_this_jobs_rows(store):
    adapter = _RecordingAdapter()
    # A standing-organize row (cleanup_job_id=None) and a cleanup-job row (job-y).
    store.ledger = [
        {
            "id": 1,
            "signal_url": "imap://s/1/1",
            "op": "label",
            "target": "L/x",
            "reverse_token": {
                "op": "label",
                "mailbox": "INBOX",
                "message_id": "<a>",
                "target": "L/x",
            },
            "cleanup_job_id": None,
            "reversed_at": None,
        },
        {
            "id": 2,
            "signal_url": "imap://s/1/2",
            "op": "move",
            "target": "L/y",
            "reverse_token": {
                "op": "move",
                "mailbox": "INBOX",
                "message_id": "<b>",
                "target": "L/y",
            },
            "cleanup_job_id": "job-y",
            "reversed_at": None,
        },
    ]
    store.jobs["job-y"] = {
        "id": "job-y",
        "user_id": "u",
        "source_id": _SID,
        "kind": "window",
        "state": "undoing",
        "undo_ops": ["label", "move", "trash"],
        "undo_ticks": 0,
        "reversed": 0,
    }
    import services.email_cleanup as m

    orig = m.eo._adapter_for
    m.eo._adapter_for = lambda sid, uid: adapter
    try:
        m._run_undo(store, _Src(), dict(store.jobs["job-y"]))
    finally:
        m.eo._adapter_for = orig
    # Only the cleanup job's row was reversed; the standing row is untouched.
    assert [r["message_id"] for r in adapter.reversed] == ["<b>"]
    assert store.ledger[0]["reversed_at"] is None  # standing label still active
    assert store.ledger[1]["reversed_at"] == "now"  # cleanup move reversed
    assert store.jobs["job-y"]["state"] == "done"


def test_summary_groups_proposals(store, monkeypatch):
    monkeypatch.setattr(
        eo, "_label_for", lambda uid, slug: {"promotional_bulk": "Promotional / bulk"}.get(slug)
    )
    store.jobs["job-z"] = {
        "id": "job-z",
        "user_id": "u",
        "source_id": _SID,
        "kind": "window",
        "state": "ready",
        "backfill_cursor": 10,
        "backfill_max_uid": 10,
        "window_start": None,
        "window_end": None,
        "scanned": 3,
        "classified": 3,
        "labeled": 3,
        "executed": 0,
    }
    store.items = [
        {"job_id": "job-z", "signal_url": "u1", "category": "promotional_bulk"},
        {"job_id": "job-z", "signal_url": "u2", "category": "promotional_bulk"},
    ]
    out = ec.job_summary("job-z", "u", _SID)
    assert out["state"] == "ready" and out["progress"] == 1.0
    assert out["proposals"] == [
        {
            "category": "promotional_bulk",
            "label": "Promotional / bulk",
            "count": 2,
            "label_visible": True,
            "can_move": True,
        }
    ]
