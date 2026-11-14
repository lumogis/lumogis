# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Postgres-backed atomic claim over a **synchronous** proposal execution (LUM-123, LUM-762).

**This is a claim, not a queue.** Nothing drains ``action_proposals`` in the
background, and nothing is meant to. The single execution path is
``POST /api/v1/approvals/proposals/{id}/execute``, called by the web composer
immediately after ``approve`` — there is no surface in the product that approves
without executing. The lifecycle is one-way::

    pending → approved → executing → done | dead

There is deliberately **no** ``executing → approved`` edge, so no row can sit
``approved`` waiting for a worker that does not exist.

Until LUM-762 this module was modelled on :mod:`services.batch_queue`, which is
a real queue with a real APScheduler drain loop. Only half that shape was ever
wired: the stale-claim sweeper ran, the drain never did. ``claim_next`` had zero
production callers, and ``fail_execution`` returned failed rows to ``approved``
with an exponential ``run_after`` that nothing re-claimed — a retry ladder that
never climbed, whose only effect was to strand rows and pin their staged
attachments on disk (``approved`` is a *live* state for
``email_attachment_staging.sweep``).

**Failure is terminal on the first attempt.** :func:`dead_letter` is the sole
disposal. That is not a tightening for its own sake: a background drain gives
at-least-once delivery, at-least-once needs an idempotent consumer, and the
consumers here are a third-party SMTP server and a third-party IMAP server with
no dedupe key — ``build_message`` mints a fresh ``Message-ID`` per attempt, so a
redrive is a second distinct email, not a retry. See
``docs/decisions/040-action-proposals-atomic-claim.md`` (amended) and ADR 196.

What remains, and why:

  * :func:`claim_by_id` — the atomic claim. Reached on **every** execution, and
    load-bearing for a household: one approved proposal is visible to several
    clients and either can press Execute. Filters ``run_after`` so the field is
    never advisory.
  * :func:`mark_done`, :func:`dead_letter` — the two terminal transitions.
  * :func:`reset_stuck_claims` — stale ``executing`` / ``claimed`` rows go
    **dead** with error ``stale_executing_claim``; never re-opened to
    ``approved``, to avoid double connector side effects. This is the only
    thing that rescues a row stranded by a mid-execution kill.

``run_after`` is retained but is **no longer written by any code path here**. It
belongs to LUM-147's ``NOT_NOW`` deferral. Do not repurpose it as a retry clock.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any

import structlog
from pydantic import BaseModel

import config

ACTION_PROPOSALS_CLAIM_STUCK_AFTER_SECONDS = int(
    os.environ.get("ACTION_PROPOSALS_CLAIM_STUCK_AFTER_SECONDS", "300")
)
ACTION_PROPOSALS_CLAIM_SWEEPER_SECONDS = int(
    os.environ.get("ACTION_PROPOSALS_CLAIM_SWEEPER_SECONDS", "60")
)
STALE_EXECUTING_ERROR = "stale_executing_claim"

# LUM-762: `run_after <= NOW()` is load-bearing even though nothing in this module
# writes a future value any more. It closes the field's advisory-ness for good:
# pre-LUM-762 rows stranded by the old `fail_execution` carry a future timestamp,
# and LUM-147's NOT_NOW deferral will write it deliberately. `user_id = %s` is the
# cross-user isolation boundary — it is proven in the real-Postgres lane, because
# the unit-test fake pattern-matches this string and re-implements the check in
# Python (see tests/fakes/fake_proposal_queue_store.py).
_CLAIM_BY_ID_SQL = """
UPDATE action_proposals SET
    status = 'executing',
    claimed_at = NOW(),
    claimed_by = %s
WHERE id = %s AND user_id = %s AND status = 'approved'
  AND claimed_at IS NULL AND run_after <= NOW()
RETURNING id, user_id, action_name, payload, annotation, attempt, created_at, status
""".strip()

_MARK_DONE_SQL = """
UPDATE action_proposals SET
    status = 'done',
    executed_at = NOW(),
    finished_at = NOW()
WHERE id = %s AND status = 'executing'
""".strip()

_RESET_STUCK_DEAD_SQL = """
UPDATE action_proposals SET
    status = 'dead',
    finished_at = NOW(),
    error = %s,
    claimed_by = NULL,
    claimed_at = NULL
WHERE status IN ('executing', 'claimed')
  AND claimed_at IS NOT NULL
  AND claimed_at < NOW() - (%s * INTERVAL '1 second')
RETURNING id, user_id
""".strip()


class ClaimedProposal(BaseModel):
    id: int
    user_id: str
    action_name: str
    payload: dict[str, Any]
    attempt: int
    created_at: datetime
    status: str
    # LUM-650: the create-time risk annotation (e.g. {"first_contact": true}),
    # read by the executor's Do→Ask gate. {} when the action declared no hook.
    annotation: dict[str, Any] = {}


def _coerce_jsonb(value: Any) -> dict:
    if isinstance(value, str):
        value = json.loads(value)
    return dict(value) if isinstance(value, dict) else {}


def claim_by_id(proposal_id: int, user_id: str, worker_id: str) -> ClaimedProposal | None:
    ms = config.get_metadata_store()
    row = ms.fetch_one(_CLAIM_BY_ID_SQL, (worker_id, proposal_id, user_id))
    if not row:
        return None
    return ClaimedProposal(
        id=int(row["id"]),
        user_id=str(row["user_id"]),
        action_name=str(row["action_name"]),
        payload=_coerce_jsonb(row["payload"]),
        attempt=int(row["attempt"]),
        created_at=row["created_at"],
        status=str(row["status"]),
        annotation=_coerce_jsonb(row.get("annotation")),
    )


def mark_done(proposal_id: int) -> None:
    ms = config.get_metadata_store()
    ms.execute(_MARK_DONE_SQL, (proposal_id,))


_DEAD_LETTER_SQL = """
UPDATE action_proposals SET
    attempt = attempt + 1,
    error = LEFT(%s, 1000),
    status = 'dead',
    finished_at = NOW(),
    claimed_by = NULL,
    claimed_at = NULL,
    executed_at = NULL
WHERE id = %s AND status = 'executing'
""".strip()


def dead_letter(proposal_id: int, error: str) -> None:
    """Terminate an ``executing`` row as ``dead`` — the **sole** disposal for a
    failed proposal (LUM-762).

    Every failure lands here: permanent (:class:`PermanentActionError` — a stale
    UID, an unsupported capability), non-permanent (any other handler exception,
    swallowed by ``actions.executor`` into ``success=False``), and unknown-action.
    There is no retry branch and no re-approve, because there is no background
    worker to re-claim the row and — for the SMTP path — a redrive would be a
    duplicate outgoing email rather than a retry.

    ``run_after`` is deliberately **not** stamped: the field belongs to LUM-147's
    ``NOT_NOW`` deferral, and a dead row's deferral time is not this code's to
    decide.

    ``attempt`` is bumped, so a ``dead`` row reads ``attempt = 1``. Note that
    :func:`reset_stuck_claims` does **not** bump it, so a swept row reads ``0``
    despite having genuinely been attempted — LUM-793 must not use the counter to
    tell the two apart (use ``error``).
    """
    ms = config.get_metadata_store()
    ms.execute(_DEAD_LETTER_SQL, (error, proposal_id))


def reset_stuck_claims(*, stuck_after_seconds: int) -> int:
    """Dead-letter stale ``executing`` / ``claimed`` rows; emit audit mirrors."""

    ms = config.get_metadata_store()
    rows = ms.fetch_all(
        _RESET_STUCK_DEAD_SQL,
        (STALE_EXECUTING_ERROR, stuck_after_seconds),
    )
    audit = structlog.get_logger("lumogis.audit")
    for row in rows or []:
        audit.warning(
            "audit.proposal.stale_executing_swept",
            proposal_id=int(row["id"]),
            user_id=str(row["user_id"]),
        )
    return len(rows or [])


def select_proposal_for_user(proposal_id: int, user_id: str) -> dict | None:
    """Return lifecycle columns for dispatcher (404 / 400 / 409 / pre-flight)."""
    ms = config.get_metadata_store()
    return ms.fetch_one(
        "SELECT id, status, action_name, payload, claimed_at, claimed_by, run_after "
        "FROM action_proposals WHERE id = %s AND user_id = %s",
        (proposal_id, user_id),
    )


# Exposed for the real-Postgres lane (`test_proposal_queue_migration`). These are
# the ONLY layer that can prove SQL behaviour: the unit-test fake pattern-matches
# these strings and re-implements the logic in Python, so a fake-lane assertion
# proves the fake, not the statement (LUM-771's lesson, LUM-762's application).
CLAIM_BY_ID_SQL = _CLAIM_BY_ID_SQL
DEAD_LETTER_SQL = _DEAD_LETTER_SQL
RESET_STUCK_DEAD_SQL = _RESET_STUCK_DEAD_SQL
