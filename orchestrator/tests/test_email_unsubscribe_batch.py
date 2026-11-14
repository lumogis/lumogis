# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""LUM-710 — off-thread batch unsubscribe execution with per-target progress.

The send topology and per-target stamping are pinned in
``test_email_unsubscribe_action.py``, against the loop this shares with the
inline path. What is pinned *here* is everything that only became true once
execution left the request thread:

* the action **enqueues instead of sending**, and never before the ownership gate;
* it **sends inline when the queue is disabled**, because an enqueue with no
  worker is a success the user can see and a message that never leaves;
* the queued payload survives a JSONB round-trip and is **re-validated on claim**,
  so a row edited in the database cannot become a send;
* ownership is **re-checked at run time** and a lost mailbox is a terminal no-op,
  not a retry-to-dead;
* a mid-batch failure **propagates** so the queue retries, and the retry resumes
  rather than replays;
* progress is **per target**, owner-scoped, and 404s identically for a foreign
  job and a nonexistent one.
"""

from __future__ import annotations

import json

import pytest
from actions.errors import PermanentActionError
from actions.handlers import smtp_send
from pydantic import ValidationError
from services.batch_handlers import email_unsubscribe_batch as handler

import config
from services import email_unsubscribe

_UID = "user-A"
_SID = "11111111-1111-1111-1111-111111111111"


def _payload(n=2):
    return {
        "source_id": _SID,
        "targets": [
            {"from_addr": f"news{i}@x.com", "mailto_addr": f"u{i}@x.com", "subject": "unsubscribe"}
            for i in range(n)
        ],
    }


class _Conn:
    host = "smtp.x.com"
    port = 587
    username = "me@x.com"
    password = "pw"
    auth_type = "app_password"
    oauth_access_token = None


def _scoped(ql: str, row: dict, params, start: int) -> bool:
    """Apply a WHERE predicate **only when the query actually states it**.

    A fake that filters by ``user_id`` in Python passes whether or not the SQL
    says ``user_id = %s`` — so it certifies a scoping guard that the fake, not
    the code, is enforcing. Deleting the predicate from the query must fail a
    test; consuming the placeholders in query order is what makes that true.
    """
    i = start
    for col in ("user_id", "kind"):
        if f"{col} = %s" in ql:
            if row[col] != params[i]:
                return False
            i += 1
    return True


class _Store:
    """Enough of the metadata store to be a queue and a target table at once."""

    def __init__(self):
        self.stamped: set[str] = set()
        self.jobs: dict[int, dict] = {}
        self._next = 1

    # -- unsubscribe targets -------------------------------------------------
    def fetch_one(self, q, params=None):
        ql = " ".join((q or "").lower().split())
        if "select unsubscribed_at from email_unsubscribe_target" in ql:
            addr = (params or ("", "", ""))[2]
            return {"unsubscribed_at": "now"} if addr in self.stamped else {"unsubscribed_at": None}
        if "insert into user_batch_jobs" in ql:
            job_id = self._next
            self._next += 1
            user_id, kind, payload_json, _run_after = params
            self.jobs[job_id] = {
                "id": job_id,
                "user_id": user_id,
                "kind": kind,
                "payload": payload_json,
                "status": "pending",
                "attempt": 0,
                "enqueued_at": "t0",
                "started_at": None,
                "finished_at": None,
            }
            return {"id": job_id}
        if "from user_batch_jobs where id" in ql:
            row = self.jobs.get(int(params[0]))
            if not row or not _scoped(ql, row, params, 1):
                return None
            return dict(row)
        return None

    def fetch_all(self, q, params=None):
        ql = " ".join((q or "").lower().split())
        if "from email_unsubscribe_target" in ql and "= any(%s)" in ql:
            _uid, _sid, addrs = params
            return [{"from_addr": a} for a in addrs if a in self.stamped]
        if "from user_batch_jobs where user_id" in ql:
            limit = params[-1]
            rows = [r for r in self.jobs.values() if _scoped(ql, r, params, 0)]
            rows.sort(key=lambda r: -r["id"])
            return [
                {
                    "id": r["id"],
                    "status": r["status"],
                    "enqueued_at": r["enqueued_at"],
                    "finished_at": r["finished_at"],
                    "total": len(json.loads(r["payload"])["targets"]),
                }
                for r in rows[:limit]
            ]
        return []

    def execute(self, q, params=None):
        ql = " ".join((q or "").lower().split())
        if "update email_unsubscribe_target set unsubscribed_at" in ql:
            self.stamped.add((params or ("", "", ""))[2])


@pytest.fixture
def env(monkeypatch):
    store = _Store()
    monkeypatch.setattr(config, "get_metadata_store", lambda: store)

    from adapters.email_smtp_sender import SendResult
    from adapters.email_smtp_sender import SmtpSender

    sends: list = []
    fail_at: dict = {"n": None}

    def _fake_send(self, msg, **kw):
        if fail_at["n"] is not None and len(sends) == fail_at["n"]:
            raise RuntimeError("smtp went away")
        sends.append(msg)
        return SendResult(message_id=msg["Message-ID"] or "<mid@x>", refused=())

    monkeypatch.setattr(SmtpSender, "send", _fake_send, raising=True)
    monkeypatch.setattr(smtp_send, "_resolve_smtp", lambda sid, uid: _Conn())
    monkeypatch.setattr(smtp_send, "_load_owned_source", lambda sid, uid: object())
    monkeypatch.setattr(
        "actions.handlers.imap_writeback._load_owned_source", lambda sid, uid: object()
    )
    # Pacing is real behaviour but wall-clock in a test is not evidence of it —
    # `test_pacing_sleeps_between_sends_only` asserts the calls instead.
    monkeypatch.setenv("EMAIL_UNSUBSCRIBE_PACE_SECONDS", "0")
    monkeypatch.delenv("BATCH_QUEUE_ENABLED", raising=False)
    return {"sends": sends, "store": store, "fail_at": fail_at}


# --------------------------------------------------------------------------- #
# the action enqueues rather than sending
# --------------------------------------------------------------------------- #
def test_execute_returns_a_job_handle_without_sending_anything(env):
    result = smtp_send._email_unsubscribe(_payload(3), user_id=_UID)

    assert result.success is True
    assert env["sends"] == [], "execute must not submit SMTP on the request thread"
    assert len(env["store"].jobs) == 1
    job = env["store"].jobs[1]
    assert job["kind"] == "email_unsubscribe" and job["user_id"] == _UID
    assert f"job {job['id']}" in result.output


def test_a_foreign_mailbox_never_reaches_the_queue(env, monkeypatch):
    """The ownership gate stayed ahead of the side effect when the side effect
    changed from a send to an enqueue."""

    def _deny(sid, uid):
        raise PermanentActionError("not_found", "not yours")

    monkeypatch.setattr(smtp_send, "_load_owned_source", _deny)
    with pytest.raises(PermanentActionError):
        smtp_send._email_unsubscribe(_payload(1), user_id="someone-else")
    assert env["store"].jobs == {}


@pytest.mark.parametrize("flag", ["false", "0", "no", "FALSE"])
def test_it_sends_inline_when_the_queue_is_disabled(env, monkeypatch, flag):
    """`main.py` only starts the worker when this is on. Enqueueing regardless
    would report a successful unsubscribe for mail that never leaves."""
    monkeypatch.setenv("BATCH_QUEUE_ENABLED", flag)

    result = smtp_send._email_unsubscribe(_payload(2), user_id=_UID)

    assert env["store"].jobs == {}, "nothing may sit in a queue no worker drains"
    assert [m["To"] for m in env["sends"]] == ["u0@x.com", "u1@x.com"]
    assert "unsubscribed from 2 sender(s)" in result.output


# --------------------------------------------------------------------------- #
# what the worker does with the queued payload
# --------------------------------------------------------------------------- #
def test_the_queued_payload_is_revalidated_on_claim(env):
    """The payload model IS the action's input model, so every recipient
    validator is re-applied to JSONB that has been at rest in the database. A row
    edited to carry a malformed recipient must fail to claim, not to send."""
    from services.batch_queue import _handlers

    _fn, model = _handlers["email_unsubscribe"]
    assert model is smtp_send.EmailUnsubscribeInput

    tampered = _payload(1)
    tampered["targets"][0]["mailto_addr"] = "not-an-address"
    with pytest.raises(ValidationError):
        model.model_validate(tampered)


def test_a_round_tripped_payload_still_sends(env):
    """Enqueue serialises to JSON; the worker reads it back. Anything that did
    not survive that would be a silent no-send in production only."""
    smtp_send._email_unsubscribe(_payload(2), user_id=_UID)
    stored = json.loads(env["store"].jobs[1]["payload"])

    handler.handle_email_unsubscribe(
        user_id=_UID,
        payload=smtp_send.EmailUnsubscribeInput.model_validate(stored),
        job_id=1,
    )
    assert [m["To"] for m in env["sends"]] == ["u0@x.com", "u1@x.com"]


def test_a_mailbox_lost_between_approval_and_run_is_a_terminal_noop(env, monkeypatch):
    """Approval and execution are no longer the same instant. Retrying cannot
    make a foreign mailbox ours, so this must return — raising would burn the
    attempts and dead-letter."""

    def _deny(sid, uid):
        raise PermanentActionError("not_found", "gone")

    monkeypatch.setattr("actions.handlers.imap_writeback._load_owned_source", _deny)

    handler.handle_email_unsubscribe(
        user_id=_UID,
        payload=smtp_send.EmailUnsubscribeInput.model_validate(_payload(2)),
        job_id=1,
    )
    assert env["sends"] == []


def test_a_midbatch_failure_propagates_so_the_queue_retries(env):
    """Swallowing it into a partial-success result would mark the job done with
    targets unsent and no retry."""
    env["fail_at"]["n"] = 2  # third send blows up

    with pytest.raises(RuntimeError):
        handler.handle_email_unsubscribe(
            user_id=_UID,
            payload=smtp_send.EmailUnsubscribeInput.model_validate(_payload(5)),
            job_id=1,
        )
    assert env["store"].stamped == {"news0@x.com", "news1@x.com"}


def test_the_retry_resumes_rather_than_replaying(env):
    env["fail_at"]["n"] = 2
    with pytest.raises(RuntimeError):
        handler.handle_email_unsubscribe(
            user_id=_UID,
            payload=smtp_send.EmailUnsubscribeInput.model_validate(_payload(5)),
            job_id=1,
        )
    env["sends"].clear()
    env["fail_at"]["n"] = None

    handler.handle_email_unsubscribe(
        user_id=_UID,
        payload=smtp_send.EmailUnsubscribeInput.model_validate(_payload(5)),
        job_id=1,
    )
    assert [m["To"] for m in env["sends"]] == ["u2@x.com", "u3@x.com", "u4@x.com"], (
        "the two already stamped must not be sent again"
    )


# --------------------------------------------------------------------------- #
# pacing
# --------------------------------------------------------------------------- #
def test_pacing_sleeps_between_sends_only(env, monkeypatch):
    """Not before the first and not after the last — otherwise the pace is a
    tax on every batch including the one-target case."""
    slept: list[float] = []
    monkeypatch.setattr(smtp_send.time, "sleep", lambda s: slept.append(s))

    smtp_send.run_unsubscribe_batch(
        smtp_send.EmailUnsubscribeInput.model_validate(_payload(4)),
        user_id=_UID,
        pace_seconds=2.0,
    )
    assert slept == [2.0, 2.0, 2.0]


def test_a_skipped_target_does_not_consume_a_pace_interval(env, monkeypatch):
    """Nothing was submitted, so there is no burst to pace. Sleeping anyway
    would make a fully-completed retry take the whole batch's wall-clock."""
    slept: list[float] = []
    monkeypatch.setattr(smtp_send.time, "sleep", lambda s: slept.append(s))
    env["store"].stamped.update({"news0@x.com", "news1@x.com", "news2@x.com"})

    smtp_send.run_unsubscribe_batch(
        smtp_send.EmailUnsubscribeInput.model_validate(_payload(4)),
        user_id=_UID,
        pace_seconds=2.0,
    )
    assert slept == [], "one send happened; nothing to pace against"


def test_the_worst_case_batch_fits_inside_the_stuck_timeout():
    """The cap stopped being a latency bound and became a *duration* bound: a run
    that outlives BATCH_QUEUE_STUCK_AFTER_SECONDS is reclaimed mid-batch by the
    sweeper. Raising either number without checking the other breaks this."""
    from services.batch_queue import BATCH_QUEUE_STUCK_AFTER_SECONDS

    per_send_seconds = 2.0  # a slow consumer SMTP submission
    pace = 2.0  # EMAIL_UNSUBSCRIBE_PACE_SECONDS default
    worst = smtp_send.MAX_UNSUBSCRIBE_BATCH * (per_send_seconds + pace)
    assert worst < BATCH_QUEUE_STUCK_AFTER_SECONDS / 2, (
        f"{smtp_send.MAX_UNSUBSCRIBE_BATCH} targets could take {worst:.0f}s; "
        f"the sweeper reclaims at {BATCH_QUEUE_STUCK_AFTER_SECONDS}s"
    )


def test_a_malformed_pace_setting_falls_back_rather_than_crashing(monkeypatch):
    monkeypatch.setenv("EMAIL_UNSUBSCRIBE_PACE_SECONDS", "soon")
    assert handler._pace_seconds() == 2.0
    monkeypatch.setenv("EMAIL_UNSUBSCRIBE_PACE_SECONDS", "-5")
    assert handler._pace_seconds() == 0.0


# --------------------------------------------------------------------------- #
# per-target progress
# --------------------------------------------------------------------------- #
def test_progress_is_per_target_not_just_running_or_done(env):
    smtp_send._email_unsubscribe(_payload(3), user_id=_UID)
    env["store"].stamped.add("news1@x.com")

    prog = email_unsubscribe.batch_job_progress(_UID, 1)

    assert prog["total"] == 3 and prog["sent"] == 1 and prog["outstanding"] == 2
    assert prog["targets"] == [
        {"from_addr": "news0@x.com", "unsubscribed": False},
        {"from_addr": "news1@x.com", "unsubscribed": True},
        {"from_addr": "news2@x.com", "unsubscribed": False},
    ]


def test_progress_never_exposes_the_queues_error_text(env):
    """`user_batch_jobs.error` is repr() of whatever the SMTP stack raised — on a
    path where the remote end is the sender being unsubscribed from."""
    smtp_send._email_unsubscribe(_payload(1), user_id=_UID)
    prog = email_unsubscribe.batch_job_progress(_UID, 1)
    assert "error" not in prog


def test_another_members_job_is_indistinguishable_from_a_missing_one(env):
    smtp_send._email_unsubscribe(_payload(1), user_id=_UID)

    assert email_unsubscribe.batch_job_progress("user-B", 1) is None
    assert email_unsubscribe.batch_job_progress(_UID, 9999) is None


def test_a_job_of_another_kind_is_not_readable_through_this_surface(env):
    """The id space is shared with every other batch kind, so without the kind
    filter this would read a session_end or ingest payload back to the user."""
    smtp_send._email_unsubscribe(_payload(1), user_id=_UID)
    env["store"].jobs[1]["kind"] = "session_end"

    assert email_unsubscribe.batch_job_progress(_UID, 1) is None


def test_recent_jobs_are_listed_newest_first_for_client_discovery(env):
    smtp_send._email_unsubscribe(_payload(1), user_id=_UID)
    smtp_send._email_unsubscribe(_payload(3), user_id=_UID)

    jobs = email_unsubscribe.list_batch_jobs(_UID)

    assert [j["job_id"] for j in jobs] == [2, 1]
    assert [j["total"] for j in jobs] == [3, 1]
    assert email_unsubscribe.list_batch_jobs("user-B") == []
