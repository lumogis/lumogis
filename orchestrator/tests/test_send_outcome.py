# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Did the stranded send actually go out? (LUM-793)

Scope note, deliberately narrow: these tests pin the **state machine** —
which evidence maps to which of the four outcomes, and in what precedence.
They do NOT pin the SQL. The fake below records the queries it was handed and
returns canned rows; it does not re-implement a WHERE clause, because a fake
that re-implements SQL is exactly what let a dropped `user_id` predicate pass
in LUM-762. Cross-user isolation lives in the real-Postgres lane
(`tests/integration/test_recoverable_sends_live.py`).
"""

from __future__ import annotations

from datetime import datetime
from datetime import timedelta
from datetime import timezone

import pytest
from services.send_outcome import NOT_SENT
from services.send_outcome import PARTIALLY_SENT
from services.send_outcome import SENT
from services.send_outcome import UNKNOWN
from services.send_outcome import resolve_send_outcome

import config
from services import send_outcome

NOW = datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc)


class FakeSentLogStore:
    """Returns canned sent-log rows and REMEMBERS which lookup was used.

    ``by_id`` / ``by_heuristic`` are the two answers; ``queries`` is what makes
    the precedence assertions real rather than inferred from the outcome.
    """

    def __init__(self, *, by_id=None, by_heuristic=None, raises=False):
        self.by_id = by_id
        self.by_heuristic = by_heuristic
        self.raises = raises
        self.queries: list[str] = []

    def ping(self) -> bool:
        return True

    def close(self) -> None:
        pass

    def fetch_one(self, sql, params=None):
        if self.raises:
            raise RuntimeError("postgres is having a day")
        q = " ".join(sql.split()).lower()
        self.queries.append(q)
        if "proposal_id = %s" in q:
            return self.by_id
        return self.by_heuristic

    def fetch_all(self, sql, params=None):
        return []


@pytest.fixture
def store():
    def _install(**kwargs):
        s = FakeSentLogStore(**kwargs)
        config._instances["metadata_store"] = s
        return s

    yield _install
    config._instances.pop("metadata_store", None)


def _proposal(**over):
    row = {
        "proposal_id": 42,
        "action_name": "email_compose",
        "payload": {
            "source_id": "src-1",
            "to": ["a@example.com"],
            "subject": "Dinner",
            "body": "…",
        },
        "error": "smtp_send_unavailable: connection reset",
        "finished_at": NOW,
    }
    row.update(over)
    return row


def _log_row(to_addrs, **over):
    row = {
        "to_addrs": to_addrs,
        "cc_addrs": [],
        "bcc_addrs": [],
        "sent_at": NOW - timedelta(seconds=2),
    }
    row.update(over)
    return row


# --------------------------------------------------------------------------- #
# The four states
# --------------------------------------------------------------------------- #


def test_outcome_sent_when_sent_log_row_exists(store):
    store(by_id=_log_row(["a@example.com"]))
    out = resolve_send_outcome(_proposal(), user_id="alice")
    assert out.state == SENT
    assert out.is_sent is True
    assert out.sent_at == NOW - timedelta(seconds=2)
    assert out.delivered_to == ["a@example.com"]


def test_outcome_not_sent_when_absent(store):
    store()
    out = resolve_send_outcome(_proposal(), user_id="alice")
    assert out.state == NOT_SENT
    assert out.is_sent is False
    assert out.sent_at is None


def test_outcome_unknown_when_resolver_raises(store):
    store(raises=True)
    out = resolve_send_outcome(_proposal(), user_id="alice")
    assert out.state == UNKNOWN
    # "Couldn't check" must never become a 500 on a listing route.


def test_outcome_not_sent_when_row_names_no_recipients(store):
    """A logged row with an empty recipient set is not evidence of delivery."""
    store(by_id=_log_row([]))
    assert resolve_send_outcome(_proposal(), user_id="alice").state == NOT_SENT


# --------------------------------------------------------------------------- #
# MUTATION I — collapse `partially_sent` into `sent`.
#
# `_finalize_send` logs the ACCEPTED recipients and *then* raises
# `recipients_refused`, so a sent-log row exists for a send that partly failed.
# A binary resolver calls that "sent", the surface offers no recovery, and the
# refused recipients silently never get the message.
# --------------------------------------------------------------------------- #


def test_partial_send_offers_recovery(store):
    store(by_id=_log_row(["a@example.com"]))
    proposal = _proposal(
        payload={
            "source_id": "src-1",
            "to": ["a@example.com", "b@example.com"],
            "subject": "Dinner",
            "body": "…",
        },
        error="recipients refused: b@example.com",
    )
    out = resolve_send_outcome(proposal, user_id="alice")

    assert out.state == PARTIALLY_SENT
    # THE load-bearing assertion: `is_sent` is what the route's 409 keys on, so
    # a partial must NOT be locked out of recovery.
    assert out.is_sent is False
    assert out.delivered_to == ["a@example.com"]
    assert out.refused == ["b@example.com"]


def test_partial_reply_is_detected_from_the_error_not_the_payload(store):
    """The case a payload-difference resolver gets WRONG.

    `email_reply` resolves its primary recipient from the signal at execute
    time, so the payload carries no `to` at all. Differencing the sent-log row
    against the payload finds "nothing missing" and reports a fully-successful
    send. The dead row's `error` names the refused address, and that is why the
    resolver reads it first.
    """
    store(by_id=_log_row(["cc-person@example.com"]))
    proposal = _proposal(
        action_name="email_reply",
        payload={"signal_id": "sig-1", "body": "…", "cc": ["cc-person@example.com"]},
        error="recipients refused: original-sender@example.com",
    )
    out = resolve_send_outcome(proposal, user_id="alice")

    assert out.state == PARTIALLY_SENT
    assert out.refused == ["original-sender@example.com"]


def test_total_refusal_has_no_log_row_and_reads_not_sent(store):
    """All recipients refused → `_finalize_send` writes NO row (the accepted set
    is empty) and still raises. Evidence absent, so: not sent."""
    store()
    proposal = _proposal(error="recipients refused: a@example.com")
    assert resolve_send_outcome(proposal, user_id="alice").state == NOT_SENT


def test_a_total_refusal_is_never_read_as_a_partial(store):
    """`"all recipients refused: …"` CONTAINS `"recipients refused: "`.

    Today the collision cannot fire — the adapter raises before
    `_finalize_send`, so a total refusal has no sent-log row and the resolver
    returns `not_sent` on the missing row. This pins the guard anyway, because
    the failure mode if it ever did fire is the loudest one this feature has:
    a message nobody received, reported as delivered to everyone.
    """
    store(by_id=_log_row(["a@example.com", "b@example.com"]))
    proposal = _proposal(
        error=f"{send_outcome.ALL_RECIPIENTS_REFUSED_PREFIX}a@example.com, b@example.com",
    )
    out = resolve_send_outcome(proposal, user_id="alice")

    assert out.refused == []
    assert out.state == SENT  # the log row is the evidence; no phantom partial
    assert (
        send_outcome.refused_from_error(f"{send_outcome.ALL_RECIPIENTS_REFUSED_PREFIX}x@y.com")
        == []
    )


def test_total_refusal_message_is_the_one_this_module_expects():
    """Reads the REAL adapter, so rewording it fails here rather than silently
    turning every future total refusal into a partial."""
    import inspect

    from adapters import email_smtp_sender

    source = inspect.getsource(email_smtp_sender)
    assert send_outcome.ALL_RECIPIENTS_REFUSED_PREFIX.rstrip() in source, (
        "adapters/email_smtp_sender.py no longer raises the total-refusal message "
        "send_outcome.ALL_RECIPIENTS_REFUSED_PREFIX guards against — the two must "
        "move together or a total refusal starts reading as a partial."
    )


def test_refused_from_error_ignores_unrelated_failures():
    assert send_outcome.refused_from_error("smtp_auth_failed: bad password") == []
    assert send_outcome.refused_from_error(None) == []
    assert send_outcome.refused_from_error("recipients refused: A@B.com, c@d.com") == [
        "a@b.com",
        "c@d.com",
    ]


# --------------------------------------------------------------------------- #
# MUTATION E — resolve by heuristic when `proposal_id` exists.
# MUTATION J — anchor the heuristic on `claimed_at` instead of `finished_at`.
# --------------------------------------------------------------------------- #


def test_outcome_prefers_proposal_id_over_heuristic(store):
    """An exact match and a CONFLICTING heuristic match: the id must win."""
    s = store(
        by_id=_log_row(["a@example.com"]),
        by_heuristic=_log_row(["someone-else@example.com"]),
    )
    out = resolve_send_outcome(_proposal(), user_id="alice")

    assert out.delivered_to == ["a@example.com"]
    # Precedence proven at the query level, not inferred: the heuristic was
    # never even run.
    assert len(s.queries) == 1
    assert "proposal_id = %s" in s.queries[0]


def test_heuristic_resolves_a_historical_row(store):
    """A pre-migration row has no `proposal_id`, so the fallback must resolve it.

    This is the test mutation J fails: EVERY dead row has `claimed_at IS NULL`
    (both `_DEAD_LETTER_SQL` and `_RESET_STUCK_DEAD_SQL` null it), so a fallback
    anchored there matches nothing, forever, and silently.
    """
    s = store(by_id=None, by_heuristic=_log_row(["a@example.com"]))
    out = resolve_send_outcome(_proposal(), user_id="alice")

    assert out.state == SENT
    assert out.delivered_to == ["a@example.com"]
    assert len(s.queries) == 2
    assert "sent_at between" in s.queries[1]


def test_heuristic_is_skipped_without_an_anchor(store):
    """No `finished_at` → no anchor → do not run an unbounded subject match.

    And, because no lookup happened at all, the answer is `unknown` rather than
    `not_sent` — see `test_unlookupable_row_is_unknown_not_not_sent`.
    """
    s = store(by_heuristic=_log_row(["a@example.com"]))
    out = resolve_send_outcome(
        {"payload": {"source_id": "src-1", "subject": "Dinner"}, "finished_at": None},
        user_id="alice",
    )

    assert out.state == UNKNOWN
    assert all("sent_at between" not in q for q in s.queries)


def test_unlookupable_row_is_unknown_not_not_sent(store):
    """`not_sent` is a POSITIVE claim — "we checked the log and it isn't there".

    A pre-migration `email_reply` has no `proposal_id` to match on AND no
    `source_id` in its payload (the mailbox comes from the signal at execute
    time), so no lookup is possible. Reporting that as "Not sent" tells the user
    something Lumogis does not know. It fails in the quiet direction rather than
    the loud one, which is exactly why it would survive review unnoticed.
    """
    s = store(by_heuristic=_log_row(["a@example.com"]))
    out = resolve_send_outcome(
        {
            "action_name": "email_reply",
            "payload": {"signal_id": "sig-1", "body": "sure"},
            "finished_at": NOW,
        },
        user_id="alice",
    )

    assert out.state == UNKNOWN
    assert out.is_sent is False  # still recoverable — the user loses nothing
    assert s.queries == [], "no lookup was possible, so none should have run"


def test_absence_after_a_real_lookup_is_not_sent(store):
    """The other side of the same rule: an exact-id lookup DID run and found
    nothing, so `not_sent` is earned rather than assumed."""
    s = store(by_id=None, by_heuristic=None)
    out = resolve_send_outcome(
        {"proposal_id": 42, "payload": {"signal_id": "sig-1"}, "finished_at": NOW},
        user_id="alice",
    )

    assert out.state == NOT_SENT
    assert any("proposal_id = %s" in q for q in s.queries)


def test_heuristic_window_is_bounded_on_both_sides(store):
    """The window is slack for clock skew, not a search range."""
    captured: list = []

    class Capturing(FakeSentLogStore):
        def fetch_one(self, sql, params=None):
            captured.append((sql, params))
            return super().fetch_one(sql, params)

    s = Capturing(by_id=None, by_heuristic=_log_row(["a@example.com"]))
    config._instances["metadata_store"] = s
    resolve_send_outcome(_proposal(), user_id="alice")

    _, params = captured[-1]
    lo, hi = params[-2], params[-1]
    assert lo == NOW - timedelta(minutes=send_outcome.HEURISTIC_WINDOW_MINUTES)
    assert hi == NOW + timedelta(minutes=send_outcome.HEURISTIC_WINDOW_MINUTES)


def test_every_lookup_filters_user_id(store):
    """Not isolation PROOF — the fake cannot provide that — but it does catch
    the predicate being deleted outright from either query."""
    s = store(by_id=None, by_heuristic=None)
    resolve_send_outcome(_proposal(), user_id="alice")
    assert s.queries
    assert all("user_id = %s" in q for q in s.queries)


# --------------------------------------------------------------------------- #
# The threading itself (LUM-793). Without this the whole chain is inert: the
# resolver's exact-match branch can only ever fire for rows the executor
# actually stamped, and a silently-unthreaded `proposal_id` degrades to the
# heuristic forever — working, slightly wrong, and invisible.
# --------------------------------------------------------------------------- #


def test_executor_passes_proposal_id_only_to_handlers_that_declare_it():
    from actions.executor import _handler_accepts_proposal_id

    def declares(input, *, user_id: str, proposal_id=None):
        return None

    def does_not(input, *, user_id: str):
        return None

    def swallows(input, **kwargs):
        return None

    assert _handler_accepts_proposal_id(declares) is True
    assert _handler_accepts_proposal_id(does_not) is False
    # **kwargs is NOT acceptance here, deliberately unlike the `user_id`
    # variant: a handler that swallows unknown kwargs would receive a value it
    # never asked for and never reads.
    assert _handler_accepts_proposal_id(swallows) is False
    # Un-introspectable → legacy, never passed.
    assert _handler_accepts_proposal_id(object()) is False


def test_the_three_send_handlers_accept_proposal_id():
    """The set that must be able to write the link, checked against the real
    handlers rather than a list in a comment."""
    from actions.executor import _handler_accepts_proposal_id
    from actions.handlers import smtp_send

    for handler in (smtp_send._email_reply, smtp_send._email_compose, smtp_send._email_forward):
        assert _handler_accepts_proposal_id(handler), handler.__name__
    # `email_unsubscribe` writes no sent-log row, so it has nothing to link.
    assert _handler_accepts_proposal_id(smtp_send._email_unsubscribe) is False


def test_finish_claim_threads_the_proposal_id_into_execute(monkeypatch):
    """`_finish_claim` is the ONLY caller that knows the id — the plan's first
    draft specified the parameter and left it with no caller."""
    from actions import proposal_execute

    from services import proposal_queue

    seen: dict = {}

    def _fake_execute(action_name, payload, **kwargs):
        seen.update(kwargs)
        from models.actions import ActionResult

        return ActionResult(success=True, output="ok")

    monkeypatch.setattr(proposal_execute, "execute", _fake_execute)
    monkeypatch.setattr(proposal_queue, "mark_done", lambda pid: None)

    claimed = proposal_queue.ClaimedProposal(
        id=4242,
        user_id="alice",
        action_name="email_compose",
        payload={"body": "hi"},
        attempt=0,
        created_at=NOW,
        status="executing",
    )
    proposal_execute._finish_claim(claimed, user_id="alice")
    assert seen.get("proposal_id") == 4242
