# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Off-thread execution of an approved unsubscribe batch (LUM-710).

LUM-697 sent the whole batch on the execute request thread, so the request cost
was proportional to the batch size and a ten-target cap was the only thing
bounding it. This runs the same send loop on the batch-queue worker instead.

**The approval step does not move.** The batch reaching this handler was already
reviewed target-by-target on the Approvals card; the payload is the one that was
approved, byte for byte, and ``batch_queue._run_one_tick`` re-validates it
against :class:`EmailUnsubscribeInput` on claim — so every recipient validator is
re-applied to JSONB that has been at rest in the database.

**Ownership is re-checked here**, not just at approve time. Approval and
execution used to be the same instant and are no longer; a mailbox can be
deleted, or change hands, in between. A source that is gone or foreign is a
terminal no-op, never a retry-to-dead — retrying cannot make it ours.

**Retry resumes, it does not replay.** ``run_unsubscribe_batch`` stamps each
target as its own send succeeds and skips targets already stamped, so a failure
at target 6 of 20 propagates, the queue re-runs with backoff, and the re-run
sends the outstanding fourteen. That is the whole reason the failure raises
rather than being swallowed into a partial-success result.

**No outbound HTTP.** This executes ``mailto:`` unsubscribes over the user's own
SMTP submission. The ``https:`` form is handed to the browser and never fetched
server-side (CVE-2023-48307); ``tests/test_unsubscribe_no_outbound_http.py``
covers this module.
"""

from __future__ import annotations

import logging
import os

from actions.errors import PermanentActionError
from actions.handlers.smtp_send import UNSUBSCRIBE_BATCH_KIND
from actions.handlers.smtp_send import EmailUnsubscribeInput
from actions.handlers.smtp_send import run_unsubscribe_batch
from services.batch_queue import register_batch_handler

_log = logging.getLogger(__name__)


def _pace_seconds() -> float:
    """Delay between consecutive submissions in one batch.

    Consumer SMTP providers throttle or spam-flag bursts, and a batch of fifty
    unsubscribes submitted back to back is exactly the shape they score. Off the
    request thread this costs nothing a user waits on. Read per call so an
    operator can tune it without a restart, and so the tests can zero it.

    **The cost, stated rather than hidden:** ``main.py``'s ``_batch_tick`` runs
    jobs synchronously under ``max_instances=1``, so this sleep holds the tick
    for its duration and delays other queued work — up to ~100 s at the 50-target
    cap. That is tolerable because ingest jobs already occupy the tick for
    minutes at a time, but it is the reason the cap and the pace are bounded
    against ``BATCH_QUEUE_STUCK_AFTER_SECONDS`` rather than left to grow. If
    unsubscribe batches ever become routine rather than occasional, the fix is to
    process a chunk and re-enqueue with ``run_after`` instead of sleeping.
    """
    try:
        return max(0.0, float(os.environ.get("EMAIL_UNSUBSCRIBE_PACE_SECONDS", "2.0")))
    except ValueError:
        return 2.0


@register_batch_handler(UNSUBSCRIBE_BATCH_KIND, EmailUnsubscribeInput)
def handle_email_unsubscribe(*, user_id: str, payload: EmailUnsubscribeInput, job_id: int) -> None:
    from actions.handlers.imap_writeback import _load_owned_source

    try:
        _load_owned_source(payload.source_id, user_id)
    except PermanentActionError:
        # Mailbox deleted or no longer this user's between approval and run.
        # Terminal: no amount of retrying makes it theirs again.
        _log.info(
            "email_unsubscribe: source gone/foreign job_id=%s — no-op",
            job_id,
            extra={"user_id": user_id, "job_id": job_id},
        )
        return

    sent, skipped = run_unsubscribe_batch(payload, user_id=user_id, pace_seconds=_pace_seconds())
    # Sender addresses are the user's own subscription list — counts only.
    _log.info(
        "email_unsubscribe: job_id=%s sent=%d already_done=%d",
        job_id,
        len(sent),
        len(skipped),
        extra={"user_id": user_id, "job_id": job_id},
    )
