# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Delivery-status notification parsing (LUM-820).

The asymmetry driving every case here: a message wrongly treated as ordinary is
merely un-correlated, while a message wrongly treated as a bounce is **hidden
from the triage lanes**. So the parser's default is always "not a DSN", and the
tests that matter most are the ones asserting it refuses.

Real-provider shapes, not idealised ones: Gmail returns `message/rfc822`,
several others return `text/rfc822-headers` only, and some return neither.
"""

from __future__ import annotations

import pytest
from services.email_dsn import parse_dsn

_HARD_BOUNCE = b"""From: Mail Delivery Subsystem <MAILER-DAEMON@gmail.com>
To: alice@example.com
Subject: Delivery Status Notification (Failure)
Content-Type: multipart/report; report-type=delivery-status; boundary="B"
MIME-Version: 1.0

--B
Content-Type: text/plain; charset=us-ascii

Your message could not be delivered. It failed.

--B
Content-Type: message/delivery-status

Reporting-MTA: dns; gmail.com

Final-Recipient: rfc822; jhon@example.com
Action: failed
Status: 5.1.1
Diagnostic-Code: smtp; 550 5.1.1 No such user

--B
Content-Type: message/rfc822

From: alice@example.com
To: jhon@example.com
Subject: Lunch?
Message-ID: <original-abc@lumogis.local>

Shall we do Tuesday?

--B--
"""

_SOFT_BOUNCE = _HARD_BOUNCE.replace(b"Status: 5.1.1", b"Status: 4.2.2").replace(
    b"550 5.1.1 No such user", b"452 4.2.2 Mailbox full"
)

_HEADERS_ONLY = b"""From: postmaster@corp.example
To: alice@example.com
Content-Type: multipart/report; report-type=delivery-status; boundary="Z"
MIME-Version: 1.0

--Z
Content-Type: message/delivery-status

Final-Recipient: rfc822; nobody@corp.example
Status: 5.4.1

--Z
Content-Type: text/rfc822-headers

From: alice@example.com
To: nobody@corp.example
Message-ID: <headers-only-xyz@lumogis.local>
Subject: Invoice

--Z--
"""

_ORDINARY_MAIL = b"""From: bob@example.com
To: alice@example.com
Subject: Re: your message failed to arrive
Content-Type: text/plain

Status: 5.1.1 is a thing I read about. MAILER-DAEMON never wrote to me though.
"""


# ---------------------------------------------------------------------------
# Recognising a real bounce
# ---------------------------------------------------------------------------


def test_a_hard_bounce_is_recognised_and_correlated():
    report = parse_dsn(_HARD_BOUNCE)

    assert report is not None
    assert report.permanent is True
    assert report.status == "5.1.1"
    assert report.original_message_id == "<original-abc@lumogis.local>"
    assert report.failed_recipient == "jhon@example.com"


def test_a_soft_bounce_is_recognised_but_not_permanent():
    """4.x.x is a retry in progress -- alarming the user here trains them to
    ignore the notice that actually matters."""
    report = parse_dsn(_SOFT_BOUNCE)

    assert report is not None
    assert report.permanent is False
    assert report.status == "4.2.2"


def test_the_original_is_found_in_a_headers_only_report():
    """Several providers return `text/rfc822-headers` rather than the message."""
    report = parse_dsn(_HEADERS_ONLY)

    assert report is not None
    assert report.original_message_id == "<headers-only-xyz@lumogis.local>"


def test_hard_and_soft_are_told_apart_by_status_not_prose():
    """Both bodies say "failed"; only the Status field differs."""
    assert b"It failed." in _HARD_BOUNCE and b"It failed." in _SOFT_BOUNCE

    assert parse_dsn(_HARD_BOUNCE).permanent is True
    assert parse_dsn(_SOFT_BOUNCE).permanent is False


# ---------------------------------------------------------------------------
# Refusing -- the direction that must never be wrong
# ---------------------------------------------------------------------------


def test_ordinary_mail_mentioning_a_status_code_is_not_a_bounce():
    """Matching on prose or on MAILER-DAEMON would hide this from the lanes."""
    assert parse_dsn(_ORDINARY_MAIL) is None


def test_a_report_with_no_machine_readable_status_stays_ordinary_mail():
    """Structurally a report, but nothing actionable -- and guessing from the
    prose is exactly what this module exists to avoid."""
    no_status = _HARD_BOUNCE.replace(b"Status: 5.1.1\n", b"")

    assert parse_dsn(no_status) is None


def test_a_multipart_that_is_not_a_delivery_status_report_is_ignored():
    """`report-type` is checked, not just `multipart/report`."""
    disposition = _HARD_BOUNCE.replace(
        b"report-type=delivery-status", b"report-type=disposition-notification"
    )

    assert parse_dsn(disposition) is None


@pytest.mark.parametrize("raw", [None, b"", b"not a message at all", b"\x00\xff\x00"])
def test_garbage_never_raises_and_never_claims_a_bounce(raw):
    """An exception here would turn one malformed message into a stalled poll."""
    assert parse_dsn(raw) is None


def test_a_bounce_without_the_original_still_parses():
    """Correlation failure is not parse failure.

    Some providers return neither the message nor its headers. The report is
    still a DSN and must still be kept out of the lanes; it simply cannot be
    tied to a send.
    """
    stripped = _HARD_BOUNCE.split(b"--B\nContent-Type: message/rfc822")[0] + b"--B--\n"

    report = parse_dsn(stripped)

    assert report is not None
    assert report.original_message_id == ""
    assert report.permanent is True


# ---------------------------------------------------------------------------
# Correlation -- tying the bounce back to the send
# ---------------------------------------------------------------------------


class _Store:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.queries = []

    def fetch_one(self, sql, params=None):
        self.queries.append((sql, params))
        uid, mid = params
        for r in self.rows:
            if r["user_id"] == uid and r["message_id"] == mid:
                return dict(r)
        return None

    def fetch_all(self, sql, params=None):
        return []

    def execute(self, sql, params=None):
        return None


@pytest.fixture
def store(monkeypatch):
    import config

    s = _Store()
    monkeypatch.setattr(config, "get_metadata_store", lambda: s)
    return s


def test_a_bounce_correlates_to_the_send_it_reports_on(store):
    from services.email_dsn import correlate

    store.rows = [
        {
            "user_id": "alice",
            "message_id": "<original-abc@lumogis.local>",
            "id": "send-1",
            "subject": "Lunch?",
            "to_addrs": ["jhon@example.com"],
            "sent_at": None,
        }
    ]
    report = parse_dsn(_HARD_BOUNCE)

    assert correlate(report, "alice")["subject"] == "Lunch?"


def test_a_bounce_never_correlates_against_another_members_send(store):
    """Otherwise Bob is told a message he never wrote failed to arrive."""
    from services.email_dsn import correlate

    store.rows = [
        {
            "user_id": "bob",
            "message_id": "<original-abc@lumogis.local>",
            "id": "send-1",
            "subject": "Bobs mail",
            "to_addrs": [],
            "sent_at": None,
        }
    ]

    assert correlate(parse_dsn(_HARD_BOUNCE), "alice") is None


def test_an_uncorrelated_bounce_is_a_safe_none(store):
    """Required by the ticket: an unmatched DSN stays an ordinary message
    rather than being dropped. Losing a bounce is worse than not tying it."""
    from services.email_dsn import correlate

    assert correlate(parse_dsn(_HARD_BOUNCE), "alice") is None


def test_a_report_with_no_original_message_id_does_not_query_at_all(store):
    from services.email_dsn import DsnReport
    from services.email_dsn import correlate

    assert correlate(DsnReport(True, "5.1.1", "", "x@y.example"), "alice") is None
    assert store.queries == []


# ---------------------------------------------------------------------------
# record_bounce -- the handled/not-handled contract feed_monitor depends on
# ---------------------------------------------------------------------------


def test_a_correlated_bounce_is_recorded_and_reported_handled(store):
    """True tells feed_monitor to keep it OUT of the triage lanes."""
    from services.email_dsn import record_bounce

    store.rows = [
        {
            "user_id": "alice",
            "message_id": "<original-abc@lumogis.local>",
            "id": "send-1",
            "subject": "Lunch?",
            "to_addrs": [],
            "sent_at": None,
        }
    ]

    assert record_bounce(parse_dsn(_HARD_BOUNCE), "alice") is True


def test_an_uncorrelated_bounce_reports_NOT_handled(store):
    """False sends it down the ordinary path, deliberately.

    Swallowing an unmatched DSN would be a bounce the user can never see --
    worse than one sitting awkwardly in a lane. The ticket requires this.
    """
    from services.email_dsn import record_bounce

    assert record_bounce(parse_dsn(_HARD_BOUNCE), "alice") is False


def test_a_failed_insert_falls_back_to_the_ordinary_path(store, monkeypatch):
    """A storage blip must not make a bounce vanish."""
    from services.email_dsn import record_bounce

    store.rows = [
        {
            "user_id": "alice",
            "message_id": "<original-abc@lumogis.local>",
            "id": "send-1",
            "subject": "Lunch?",
            "to_addrs": [],
            "sent_at": None,
        }
    ]

    def _boom(sql, params=None):
        raise RuntimeError("postgres is having a day")

    monkeypatch.setattr(store, "execute", _boom)

    assert record_bounce(parse_dsn(_HARD_BOUNCE), "alice") is False


def test_a_soft_bounce_is_recorded_as_non_permanent(store):
    """So a surface can say "still trying" instead of "failed"."""
    from services.email_dsn import record_bounce

    written: list = []
    store.rows = [
        {
            "user_id": "alice",
            "message_id": "<original-abc@lumogis.local>",
            "id": "send-1",
            "subject": "Lunch?",
            "to_addrs": [],
            "sent_at": None,
        }
    ]
    store.execute = lambda sql, params=None: written.append(params)

    record_bounce(parse_dsn(_SOFT_BOUNCE), "alice")

    assert written and written[0][-1] is False, "4.x.x must not be stored as permanent"


def test_the_insert_dedupes_a_redelivered_report(store):
    """Providers re-deliver; the same bounce must not surface twice."""
    from services.email_dsn import record_bounce

    seen: list = []
    store.rows = [
        {
            "user_id": "alice",
            "message_id": "<original-abc@lumogis.local>",
            "id": "send-1",
            "subject": "Lunch?",
            "to_addrs": [],
            "sent_at": None,
        }
    ]
    store.execute = lambda sql, params=None: seen.append(sql)

    record_bounce(parse_dsn(_HARD_BOUNCE), "alice")

    assert "ON CONFLICT" in seen[0] and "DO NOTHING" in seen[0]


# ---------------------------------------------------------------------------
# The surface -- the half that actually tells the user
# ---------------------------------------------------------------------------


class _ListStore:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.sql: list[str] = []
        self.updates: list[tuple] = []

    def fetch_all(self, sql, params=None):
        self.sql.append(sql)
        return list(self.rows)

    def fetch_one(self, sql, params=None):
        self.sql.append(sql)
        return {"ok": 1} if self.rows else None

    def execute(self, sql, params=None):
        self.updates.append((sql, params))


@pytest.fixture
def list_store(monkeypatch):
    import config

    s = _ListStore()
    monkeypatch.setattr(config, "get_metadata_store", lambda: s)
    return s


def test_the_surface_asks_only_for_permanent_unacknowledged_bounces(list_store):
    """A 4.x.x is still being retried; warning about it is how a warning
    becomes wallpaper, and the next real bounce lands in a notice nobody
    reads any more."""
    from services.email_dsn import list_unacknowledged

    list_unacknowledged("alice")

    sql = list_store.sql[0]
    assert "acknowledged_at IS NULL" in sql
    assert "b.permanent" in sql
    assert "user_id = %s" in sql


def test_the_surface_names_the_message_not_the_message_id(list_store):
    """ "your reply to Lunch? did not reach jhon@" is actionable; an opaque
    identifier is not."""
    from services.email_dsn import list_unacknowledged

    list_store.rows = [
        {
            "id": "b1",
            "recipient": "jhon@example.com",
            "status": "5.1.1",
            "seen_at": None,
            "sent_at": None,
            "subject": "Lunch?",
        }
    ]

    out = list_unacknowledged("alice")

    assert out[0]["subject"] == "Lunch?"
    assert "LEFT JOIN email_sent_log" in list_store.sql[0], (
        "a pruned send row must not hide the bounce"
    )


def test_acknowledging_someone_elses_bounce_is_a_no_op(list_store):
    from services.email_dsn import acknowledge

    list_store.rows = []  # ownership check finds nothing

    assert acknowledge("b1", "alice") is False
    assert list_store.updates == []


def test_acknowledging_your_own_bounce_marks_it_told(list_store):
    from services.email_dsn import acknowledge

    list_store.rows = [{"id": "b1"}]

    assert acknowledge("b1", "alice") is True
    sql, params = list_store.updates[0]
    assert "acknowledged_at = NOW()" in sql
    assert "acknowledged_at IS NULL" in sql, "re-acknowledging must not move the timestamp"
