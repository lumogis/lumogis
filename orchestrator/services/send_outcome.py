# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Did this failed send actually go out? (LUM-793)

A proposal that dead-lettered is *not* the same as a message that was never
delivered. ``smtp_send._finalize_send`` writes ``email_sent_log`` at **submission
time**, inside the handler, right after ``sender.send()`` returns — long before
``mark_done``. So the sent log is evidence that survives every way a proposal can
be stranded, and the recovery surface can answer the user's actual question
("did it send?") instead of hedging at them.

**Four states, not three.** Re-derived from ``_finalize_send`` rather than from
the plan's summary of it, because the whole correctness of this module rests on
when a row gets written:

* full success → row written for every recipient, action succeeds → ``sent``
* **partial refusal → row written for the ACCEPTED recipients, and THEN
  ``PermanentActionError("recipients_refused")`` dead-letters the proposal** →
  ``partially_sent``
* total refusal → the adapter raised before ``_finalize_send``, no row → ``not_sent``
* SMTP/auth/config failure → raised before ``_finalize_send``, no row → ``not_sent``

The partial is why a binary resolver is a **bug, not a simplification**: it reads
the row, says "sent", the surface offers no recovery, and the recipients who were
refused silently never get the message. That is the worst outcome this feature
can produce, so ``partially_sent`` names the refused set and keeps recovery open.

**Precedence: proposal_id, then heuristic, then unknown.** Migration 072 added
``email_sent_log.proposal_id``, so post-migration rows resolve by exact id. Rows
written before it fall back to a bounded match on user + source + subject +
recipients near ``finished_at``.

**The fallback anchors on ``finished_at``, NEVER ``claimed_at``.** Both disposal
statements NULL the claim (``proposal_queue._DEAD_LETTER_SQL`` and
``_RESET_STUCK_DEAD_SQL``), so *every* dead row has ``claimed_at IS NULL`` and a
fallback keyed on it would match nothing, forever, presenting as "historical rows
can't be resolved" rather than as the bug it is.

Single responsibility: answer "did this go out, and to whom?". Nothing else.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from dataclasses import field
from datetime import datetime
from datetime import timedelta
from datetime import timezone

import config

_log = logging.getLogger(__name__)

# The heuristic's half-width around `finished_at`. A send is submitted and the
# proposal disposed within the same request, so the true gap is milliseconds;
# this is slack for clock skew and a slow SMTP submission, not a search window.
HEURISTIC_WINDOW_MINUTES = 10

# The message text `smtp_send._finalize_send` raises on a partial refusal. It is
# defined HERE, and imported by the handler, because this module is the only
# reader of it and the coupling must break loudly if the wording changes.
#
# `dead_letter` persists `ActionResult.error` (= `str(exc)`) and NOT
# `ActionResult.code`, so the machine token `recipients_refused` never reaches
# the row — this prose prefix is the only thing on the proposal that says a
# partial happened. `error` is stored as `LEFT(%s, 1000)`: the prefix is at the
# front so classification always survives, though a pathological recipient list
# can truncate the tail of the names.
RECIPIENTS_REFUSED_PREFIX = "recipients refused: "

# The TOTAL-refusal message, raised by ``adapters/email_smtp_sender.py`` when the
# server rejects *every* recipient. Spelled out here for one reason: it
# **contains the partial prefix as a substring**, so a naive ``find`` on the
# partial matches it too.
#
# That is a live trap rather than a hypothetical. The adapter raises *before*
# ``_finalize_send``, so today no sent-log row accompanies a total refusal and
# the resolver reaches ``not_sent`` on the missing row alone — the collision
# never fires. Any future path that logged before raising would flip a total
# refusal to ``partially_sent`` with a full "delivered" list, and nothing would
# have complained. Checking it explicitly costs one branch.
#
# Duplicated rather than imported by the adapter, which is outside this chunk's
# scope; `test_total_refusal_message_is_the_one_this_module_expects` reads the
# real adapter's message, so a reworded string fails here loudly.
ALL_RECIPIENTS_REFUSED_PREFIX = "all recipients refused: "

SENT = "sent"
PARTIALLY_SENT = "partially_sent"
NOT_SENT = "not_sent"
UNKNOWN = "unknown"


@dataclass(frozen=True)
class SendOutcome:
    """What happened to a stranded send.

    ``delivered_to`` is populated for ``sent`` and ``partially_sent`` (the
    addresses the sent log records as ACCEPTED). ``refused`` is populated for
    ``partially_sent`` only — the payload's recipients minus the delivered set.
    """

    state: str
    sent_at: datetime | None = None
    delivered_to: list[str] = field(default_factory=list)
    refused: list[str] = field(default_factory=list)

    @property
    def is_sent(self) -> bool:
        """Fully sent — the ONLY state that refuses recovery.

        Deliberately not ``state in (SENT, PARTIALLY_SENT)``: a partial send is
        exactly the case that still needs recovering.
        """
        return self.state == SENT

    def as_dict(self) -> dict:
        return {
            "outcome": self.state,
            "sent_at": self.sent_at.isoformat() if self.sent_at else None,
            "delivered_to": list(self.delivered_to),
            "refused": list(self.refused),
        }


def _normalize(addr) -> str:
    return str(addr or "").strip().lower()


def _payload_recipients(payload: dict | None) -> list[str]:
    """Every address the payload intended to reach, across to/cc/bcc.

    LUM-698's reconciliation spans the full envelope, so the refused set must
    too — reconciling ``to`` alone would report a refused cc as delivered.
    """
    if not isinstance(payload, dict):
        return []
    out: list[str] = []
    for key in ("to", "cc", "bcc"):
        value = payload.get(key)
        if isinstance(value, str):
            value = [value]
        for addr in value or []:
            norm = _normalize(addr)
            if norm and norm not in out:
                out.append(norm)
    return out


def _as_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def refused_from_error(error: str | None) -> list[str]:
    """The refused addresses named by a dead proposal's ``error``, if any.

    This is the AUTHORITATIVE partial signal, and differencing the sent-log row
    against the payload is only a fallback, because for ``email_reply`` the
    payload has **no recipients at all** — the `to` is resolved from the signal
    at execute time. A reply whose primary recipient was refused and whose cc was
    accepted would difference to "nothing missing" and be reported as fully sent,
    which is precisely the failure `partially_sent` exists to prevent.
    """
    text = str(error or "")
    if ALL_RECIPIENTS_REFUSED_PREFIX in text:
        # A TOTAL refusal is not a partial. Checked first because the total
        # message contains the partial prefix, so order is the whole guard.
        return []
    idx = text.find(RECIPIENTS_REFUSED_PREFIX)
    if idx < 0:
        return []
    tail = text[idx + len(RECIPIENTS_REFUSED_PREFIX) :]
    out: list[str] = []
    for part in tail.split(","):
        norm = _normalize(part)
        if norm and norm not in out:
            out.append(norm)
    return out


def _row_addresses(row: dict) -> list[str]:
    out: list[str] = []
    for key in ("to_addrs", "cc_addrs", "bcc_addrs"):
        for addr in row.get(key) or []:
            norm = _normalize(addr)
            if norm and norm not in out:
                out.append(norm)
    return out


def _lookup_by_proposal_id(ms, proposal_id: int, user_id: str) -> dict | None:
    """Exact match. ``user_id`` is in the WHERE clause even though the id is
    unique — this module reads message recipients, and an id filter alone would
    make it a cross-user oracle for anyone who can guess an integer."""
    return ms.fetch_one(
        "SELECT to_addrs, cc_addrs, bcc_addrs, sent_at FROM email_sent_log "
        "WHERE proposal_id = %s AND user_id = %s "
        "ORDER BY sent_at DESC LIMIT 1",
        (proposal_id, user_id),
    )


def _lookup_by_heuristic(
    ms,
    *,
    user_id: str,
    payload: dict,
    finished_at: datetime | None,
) -> tuple[dict | None, bool]:
    """Pre-migration fallback: same user, same mailbox, same subject, near
    ``finished_at``. Bounded on both sides so it never degrades into a scan.

    Returns ``(row, ran)``. **``ran`` is the load-bearing half.** The lookup is
    skipped entirely when there is nothing safe to key on:

    * no ``finished_at`` — no anchor, and an unanchored subject match across all
      history is far more likely to mis-report than to help;
    * no ``source_id`` — which is **not** an edge case: only ``email_compose``
      carries one. ``email_reply`` and ``email_forward`` carry ``signal_id``,
      and their mailbox is resolved from the signal at execute time.

    A skipped lookup must not be reported as ``not_sent``. "We found no record"
    and "we could not look" are different claims, and only the first is evidence
    the message stayed put — see :func:`resolve_send_outcome`.
    """
    anchor = _as_utc(finished_at)
    if anchor is None:
        return None, False
    source_id = payload.get("source_id")
    if not source_id:
        return None, False
    slack = timedelta(minutes=HEURISTIC_WINDOW_MINUTES)
    row = ms.fetch_one(
        "SELECT to_addrs, cc_addrs, bcc_addrs, sent_at FROM email_sent_log "
        # SCOPE-EXEMPT: email_sent_log is per-user (no scope column) — a send
        # is only ever evidence for the member whose mailbox made it.
        "WHERE user_id = %s AND source_id = %s AND subject = %s "
        "AND sent_at BETWEEN %s AND %s "
        "ORDER BY sent_at DESC LIMIT 1",
        (
            user_id,
            str(source_id),
            str(payload.get("subject") or ""),
            anchor - slack,
            anchor + slack,
        ),
    )
    return row, True


def resolve_send_outcome(proposal: dict, *, user_id: str) -> SendOutcome:
    """Resolve one stranded send against ``email_sent_log``.

    Never raises: any failure resolves to ``unknown``, because "we couldn't
    check" is an honest answer and a 500 on a listing route is not. The caller
    renders ``unknown`` as "couldn't confirm" and still offers recovery.
    """
    try:
        ms = config.get_metadata_store()
        payload = proposal.get("payload") or {}
        if not isinstance(payload, dict):
            payload = {}
        proposal_id = proposal.get("proposal_id") or proposal.get("id")

        row = None
        # Did we actually get to ASK the question? An exact-id lookup always
        # does; the heuristic often cannot (see `_lookup_by_heuristic`).
        looked = False
        if proposal_id is not None:
            row = _lookup_by_proposal_id(ms, int(proposal_id), user_id)
            looked = True
        if row is None:
            # Only reached when the exact match found nothing: a pre-migration
            # row, or a genuine non-send. Mutation E ("resolve by heuristic when
            # proposal_id exists") is killed by this ordering, not by a flag.
            row, ran = _lookup_by_heuristic(
                ms,
                user_id=user_id,
                payload=payload,
                finished_at=proposal.get("finished_at"),
            )
            looked = looked or ran
        if row is None:
            # `not_sent` is a POSITIVE claim — "we checked the send log and this
            # message is not in it". Where no lookup was possible at all (a
            # pre-migration reply or forward, whose payload names a signal
            # rather than a mailbox) the honest answer is `unknown`. Saying
            # "Not sent" about a message we never looked up is the same class of
            # error as saying "Sent" about one that wasn't — it just fails in
            # the quieter direction. Both still offer recovery, so the user
            # loses nothing either way; what changes is whether Lumogis tells
            # them something it does not know.
            return SendOutcome(state=NOT_SENT if looked else UNKNOWN)

        delivered = _row_addresses(row)
        # Authoritative first (works for replies, which carry no payload `to`),
        # payload-difference only as a fallback for a row whose error text was
        # lost or rewritten.
        refused = refused_from_error(proposal.get("error"))
        if not refused:
            intended = _payload_recipients(payload)
            refused = [a for a in intended if a not in delivered]
        sent_at = _as_utc(row.get("sent_at"))

        if refused and delivered:
            return SendOutcome(
                state=PARTIALLY_SENT,
                sent_at=sent_at,
                delivered_to=delivered,
                refused=refused,
            )
        if not delivered:
            # A logged row with no recipients at all is not evidence of delivery.
            return SendOutcome(state=NOT_SENT)
        return SendOutcome(state=SENT, sent_at=sent_at, delivered_to=delivered)
    except Exception:  # noqa: BLE001 - "couldn't check" is a state, not a 500
        _log.warning(
            "send_outcome: could not resolve proposal=%s; reporting unknown",
            proposal.get("proposal_id") or proposal.get("id"),
            exc_info=True,
        )
        return SendOutcome(state=UNKNOWN)
