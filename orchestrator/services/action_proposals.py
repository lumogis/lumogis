# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Ask/Do proposal *producer* — the enqueue/approve half of the act tier (LUM-623).

The existing ``services.proposal_queue`` + ``actions.proposal_execute`` only cover
the *execute* half (claim an ``approved`` row and run it). Nothing in production
inserted ``action_proposals`` rows or transitioned ``pending → approved``. This
module is that missing, **connector-agnostic** half — it imports no adapter and
is the reusable template for every future act-tier action:

* ``create_proposal`` — validate the action + (generically) its payload against
  the ``ActionSpec.input_model``, reject hard-limited types, INSERT ``pending``.
* ``approve_proposal`` — owner-guarded ``pending → approved`` transition.
* ``list_pending`` / ``get_proposal`` — the caller's own rows.

Ownership of a connector-specific resource (e.g. that an IMAP signal's source is
owned by the caller) is enforced by the *handler* at execute time, not here — so
the producer stays generic. The create-time gate is shape-only (``extra="forbid"``
via each action's ``input_model``), which rejects a smuggled ``user_id`` at 422.
"""

from __future__ import annotations

import json
import logging
import os

from actions.executor import is_hard_limited
from actions.registry import get_action
from fastapi import HTTPException
from fastapi import status
from pydantic import BaseModel
from pydantic import ValidationError

import config

_log = logging.getLogger(__name__)


def create_proposal(action_name: str, payload: dict, *, user_id: str) -> int:
    """Insert a ``pending`` proposal for a registered, non-hard-limited action.

    Raises ``HTTPException`` 400 (unknown action), 403 (hard-limited), or 422
    (payload fails the action's ``input_model`` — including a smuggled field
    under ``extra="forbid"``). Returns the new proposal id.
    """
    if not isinstance(user_id, str) or not user_id:
        raise TypeError("create_proposal: user_id (keyword-only) is required")

    spec = get_action(action_name)
    if spec is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail={"error": "unknown_action", "action_name": action_name},
        )
    if is_hard_limited(spec.action_type):
        # Mirrors the execute-side pre-flight — a hard-limited action can never
        # be routed through the generic approve→execute flow.
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail={"error": "hard_limited_proposal", "action_name": spec.name},
        )

    # Only client-initiable actions are proposable. An action with no
    # ``input_model`` has no declared client payload contract — that is how
    # internal-only actions (e.g. the ``imap_writeback_reverse`` inverse, which
    # must be reached ONLY via ``attempt_reverse`` with a token-authenticated
    # audit row) are kept out of the producer. Without this gate a caller could
    # POST /proposals for the reverse action with an arbitrary hand-crafted
    # reverse payload and drive a mutation that bypasses signal→UID attribution
    # (LUM-623 code-review #1).
    model = getattr(spec, "input_model", None)
    if not (isinstance(model, type) and issubclass(model, BaseModel)):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail={"error": "not_proposable", "action_name": spec.name},
        )
    try:
        model.model_validate(payload)
    except ValidationError:
        # extra="forbid" rejects a smuggled user_id/source_id/uid here.
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": "invalid_payload", "action_name": spec.name},
        ) from None

    # Create-time risk annotation (LUM-650). The producer stays generic — it just
    # invokes the ActionSpec's hook if one is declared and persists the result on
    # the proposal's `annotation` column, so the Ask/Do resolver can read an
    # approval-relevant fact (e.g. first_contact) BEFORE the Do/Ask decision.
    #
    # FAIL CLOSED: a security default must be safe-by-omission. If the hook raises
    # or returns a non-dict, store {"first_contact": true} so an error path can
    # never downgrade a first-contact send to auto-approve.
    annotation = _compute_annotation(spec, payload, user_id)

    # LUM-695: staged-attachment ownership is checked HERE, before the row
    # exists. It cannot be checked by the model above — `model_validate` is all
    # `create_proposal` runs, and a Pydantic model cannot query the DB — and it
    # cannot wait until execute, where a 422 has no HTTP surface to be returned
    # on. So a foreign or missing reference is a 422 from this call, and only a
    # change BETWEEN propose and execute dead-letters.
    staged_ids = _staged_attachment_ids(payload)
    if staged_ids:
        _assert_staged_attachments_owned(staged_ids, user_id=user_id, action_name=spec.name)

    ms = config.get_metadata_store()
    row = ms.fetch_one(
        "INSERT INTO action_proposals (user_id, action_name, payload, annotation, status) "
        "VALUES (%s, %s, %s::jsonb, %s::jsonb, 'pending') RETURNING id",
        (user_id, action_name, json.dumps(payload), json.dumps(annotation)),
    )
    proposal_id = int(row["id"])

    # Bind the staged files to the proposal. This column is what the sweep keys
    # on: until it is set the files are "unreferenced" and age out on the TTL;
    # once set, a live proposal's attachments are never swept however old,
    # because proposals themselves never expire.
    if staged_ids:
        _bind_staged_attachments(staged_ids, user_id=user_id, proposal_id=proposal_id)
    return proposal_id


def _staged_attachment_ids(payload: dict) -> list[str]:
    """The ``attachment_ids`` a payload carries, if any. Shape-tolerant."""
    ids = payload.get("attachment_ids") if isinstance(payload, dict) else None
    if not isinstance(ids, list):
        return []
    return [str(i) for i in ids if i]


def _assert_staged_attachments_owned(
    staged_ids: list[str], *, user_id: str, action_name: str
) -> None:
    """422 unless every id is an unconsumed staged file belonging to ``user_id``."""
    from services import email_attachment_staging as staging

    try:
        staging.resolve_all(user_id, staged_ids)
    except staging.StagedAttachmentUnavailable:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": "attachment_unavailable", "action_name": action_name},
        ) from None


def _bind_staged_attachments(staged_ids: list[str], *, user_id: str, proposal_id: int) -> None:
    """Link staged files to the proposal that will send them (best-effort).

    Failing here would leave the files unreferenced — they would age out on the
    TTL rather than being retained for the proposal — which is a degraded but
    safe outcome. Failing the whole create instead would discard a valid
    proposal over bookkeeping.
    """
    from services import email_attachment_staging as staging

    try:
        staging.bind_to_proposal(user_id, staged_ids, proposal_id=proposal_id)
    except Exception:  # noqa: BLE001
        _log.warning(
            "action_proposals: could not bind staged attachments to proposal %s; "
            "they remain unreferenced and will age out on the TTL",
            proposal_id,
        )


def _compute_annotation(spec, payload: dict, user_id: str) -> dict:
    """Run the ActionSpec's create-time ``annotate`` hook, failing CLOSED.

    No hook → ``{}`` (no annotation to store). Hook raises or returns a non-dict
    → ``{"first_contact": true}`` (force Ask on any uncertainty). The hook must
    return only approval-relevant scalars — it runs BEFORE the execute-time
    ownership gate, so it must never surface pre-ownership recipient data.
    """
    hook = getattr(spec, "annotate", None)
    if hook is None:
        return {}
    try:
        result = hook(payload, user_id)
    except Exception:  # noqa: BLE001 - fail closed, never fail open on an error path
        _log.warning(
            "action_proposals: annotate hook raised for action=%s; failing closed",
            getattr(spec, "name", "?"),
        )
        return {"first_contact": True}
    if not isinstance(result, dict):
        return {"first_contact": True}
    return result


def approve_proposal(proposal_id: int, *, user_id: str) -> dict:
    """Owner-guarded ``pending → approved`` transition.

    404 (no oracle) when the row does not exist, is not owned by the caller, or
    is not ``pending`` — so it cannot be used to probe another user's proposals.
    """
    if not isinstance(user_id, str) or not user_id:
        raise TypeError("approve_proposal: user_id (keyword-only) is required")

    ms = config.get_metadata_store()
    row = ms.fetch_one(
        "UPDATE action_proposals SET status = 'approved' "
        "WHERE id = %s AND user_id = %s AND status = 'pending' "
        "RETURNING id, status",
        (proposal_id, user_id),
    )
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail={"error": "not_found"})
    return {"proposal_id": int(row["id"]), "status": str(row["status"])}


def cancel_proposal(proposal_id: int, *, user_id: str) -> dict:
    """Owner-guarded ``pending → rejected`` transition — a user abandoning an unsent
    proposal (LUM-682: the send composer creates a proposal at "Review"; choosing
    Save-to-Drafts or closing the card leaves it ``pending`` forever otherwise).

    404 (no oracle) when the row does not exist, is not owned by the caller, or is
    not ``pending`` — so it cannot probe another user's proposals, nor cancel one that
    is already approved/claimed/executing/done.
    """
    if not isinstance(user_id, str) or not user_id:
        raise TypeError("cancel_proposal: user_id (keyword-only) is required")

    ms = config.get_metadata_store()
    row = ms.fetch_one(
        "UPDATE action_proposals SET status = 'rejected' "
        "WHERE id = %s AND user_id = %s AND status = 'pending' "
        "RETURNING id, status",
        (proposal_id, user_id),
    )
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail={"error": "not_found"})

    # LUM-695: the user abandoned this send, so give them their disk back now
    # rather than at the next sweep tick. The sweep would catch it anyway (a
    # `rejected` proposal is terminal), but an hour of waiting for space the
    # user already released is a needless cost.
    _discard_staged_attachments(proposal_id)
    return {"proposal_id": int(row["id"]), "status": str(row["status"])}


def _discard_staged_attachments(proposal_id: int) -> None:
    """Delete any staged files bound to ``proposal_id``. Never raises.

    The proposal is already cancelled; a cleanup failure must not turn that into
    an error the user sees, and the proposal-state sweep is the backstop.
    """
    try:
        from services import email_attachment_staging as staging

        staging.discard_for_proposal(int(proposal_id))
    except Exception:  # noqa: BLE001
        _log.warning(
            "action_proposals: staged attachment cleanup failed for cancelled "
            "proposal %s; the sweep will reclaim them",
            proposal_id,
        )


def list_pending(user_id: str) -> list[dict]:
    """The caller's own ``pending`` proposals (newest first)."""
    if not isinstance(user_id, str) or not user_id:
        raise TypeError("list_pending: user_id (keyword-only) is required")
    ms = config.get_metadata_store()
    # SCOPE-EXEMPT: action_proposals is per-user (no scope column); a proposal is
    # only ever visible to its owner, so a user_id-only filter is the correct
    # isolation, not a household-visibility regression.
    rows = ms.fetch_all(
        "SELECT id, action_name, payload, annotation, status, created_at "
        "FROM action_proposals WHERE user_id = %s AND status = 'pending' "
        "ORDER BY id DESC",
        (user_id,),
    )
    out: list[dict] = []
    for r in rows or []:
        payload = r.get("payload")
        if isinstance(payload, str):
            payload = json.loads(payload)
        annotation = r.get("annotation")
        if isinstance(annotation, str):
            annotation = json.loads(annotation)
        out.append(
            {
                "proposal_id": int(r["id"]),
                "action_name": str(r["action_name"]),
                "payload": payload or {},
                "annotation": annotation if isinstance(annotation, dict) else {},
                "status": str(r["status"]),
                "created_at": r.get("created_at"),
            }
        )
    return out


# The actions a failed row can be RECOVERED from (LUM-793). Exactly three, and
# the omission is deliberate rather than an oversight: `email_unsubscribe` never
# calls `_record_sent_log` (it keeps its own per-target idempotent tracking), so
# it has no sent-log row to resolve an outcome against — and an unsubscribe is
# not a message a household member wants handed back to them. `email_save_draft`
# is absent for the same reason in reverse: a failed draft write has nothing to
# save to Drafts.
#
# Pinned by `test_unsubscribe_is_not_recoverable`, because "we forgot" and "we
# decided" look identical in a filter.
RECOVERABLE_SEND_ACTIONS: frozenset[str] = frozenset(
    {"email_reply", "email_compose", "email_forward"}
)

# Raising this is not free, and the cost is not in this query.
#
# `GET /recoverable-sends` runs ONE listing query and then, PER ROW, a
# `send_outcome.resolve_send_outcome` lookup and an `available_attachments`
# lookup — so 1 + 2N. At household scale N is 0–3 and every per-row read is a
# single indexed hit (the partial index on `email_sent_log.proposal_id`, and
# `email_attachment_staged`). At 200 it is 401 queries on a box that is also
# serving ingest.
#
# So: raise this, or widen RECOVERABLE_SENDS_WINDOW_DAYS past 30, only together
# with batching the two per-row lookups into `WHERE proposal_id = ANY(%s)`. If
# you batch, `user_id` must stay in both predicates and mutations E and F must
# still fail — the batching must not weaken outcome precedence or the
# read-from-staging rule.
RECOVERABLE_SENDS_LIMIT = 200


def _recoverable_window_days() -> int:
    """How long a failed send stays listed. Shares LUM-147's retention concern —
    if that policy grows a ``failed_send`` entry, this must read it, not compete
    with it."""
    try:
        return int(os.environ.get("RECOVERABLE_SENDS_WINDOW_DAYS", "30"))
    except ValueError:
        return 30


def list_recoverable_sends(user_id: str, *, within_days: int | None = None) -> list[dict]:
    """The caller's own **failed sends** (``status = 'dead'``), newest first.

    A new query rather than a widening of :func:`list_pending`, which is the
    ApprovalsPage contract and must keep meaning "awaiting your approval".

    Bounded on both the window and the row count: this reads message bodies, so
    an unbounded version would be both a performance and a blast-radius problem.
    """
    if not isinstance(user_id, str) or not user_id:
        raise TypeError("list_recoverable_sends: user_id (keyword-only) is required")
    days = _recoverable_window_days() if within_days is None else int(within_days)

    ms = config.get_metadata_store()
    # SCOPE-EXEMPT: as `list_pending` — action_proposals is per-user with no
    # scope column, so user_id IS the isolation. Proven in the Postgres lane,
    # not the fake: `FakeProposalQueueStore` pattern-matches SQL strings, so a
    # dropped predicate here would still pass a fake-lane test (LUM-762).
    rows = ms.fetch_all(
        "SELECT id, action_name, payload, status, error, "
        "created_at, finished_at, attempt FROM action_proposals "
        # SCOPE-EXEMPT: action_proposals is per-user (no scope column).
        "WHERE user_id = %s AND status = 'dead' "
        "AND action_name = ANY(%s) "
        "AND finished_at >= NOW() - (%s * INTERVAL '1 day') "
        "ORDER BY finished_at DESC, id DESC LIMIT %s",
        (user_id, sorted(RECOVERABLE_SEND_ACTIONS), days, RECOVERABLE_SENDS_LIMIT),
    )
    out: list[dict] = []
    for r in rows or []:
        payload = r.get("payload")
        if isinstance(payload, str):
            payload = json.loads(payload)
        out.append(
            {
                "proposal_id": int(r["id"]),
                "action_name": str(r["action_name"]),
                "payload": payload or {},
                "status": str(r["status"]),
                "error": r.get("error"),
                "created_at": r.get("created_at"),
                "finished_at": r.get("finished_at"),
                "attempt": int(r.get("attempt") or 0),
            }
        )
    return out


def dismiss_recoverable_send(proposal_id: int, *, user_id: str) -> dict:
    """Owner-guarded ``dead → archived`` — "I've dealt with this, stop showing me".

    **This is a state transition ADR-201's machine does not draw.** It is safe
    for two specific reasons, and both must stay true: it is terminal→terminal
    (``archived`` is already in ``TERMINAL_PROPOSAL_STATES``, so the attachment
    sweep and every other terminal consumer behave identically), and it runs no
    action. The transition ADR-201 forbids — ``dead → approved``, which would
    make a failed send re-executable and re-open the duplicate-email hazard
    LUM-762 closed — remains impossible, and is pinned by
    `test_dismiss_never_produces_approved`.

    Deletes nothing: the row and its body survive for audit, it just leaves the
    listing. 404 (no oracle) when absent, not owned, or not ``dead``.
    """
    if not isinstance(user_id, str) or not user_id:
        raise TypeError("dismiss_recoverable_send: user_id (keyword-only) is required")
    ms = config.get_metadata_store()
    row = ms.fetch_one(
        "UPDATE action_proposals SET status = 'archived' "
        "WHERE id = %s AND user_id = %s AND status = 'dead' "
        "RETURNING id, status",
        (proposal_id, user_id),
    )
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail={"error": "not_found"})
    return {"proposal_id": int(row["id"]), "status": str(row["status"])}


# Actions whose first-contact banner must NEVER be hidden by a display parse error
# (mirrors the annotate hook, which fails closed to first_contact=True) — LUM-652.
_FIRST_CONTACT_SEND_ACTIONS = frozenset({"email_reply", "email_compose"})


def first_contact_for_display(action_name: str, annotation: dict | None) -> bool:
    """Display-only first-contact flag for a proposal, **fail-closed for sends**.

    When the annotation carries an explicit ``first_contact`` boolean, use it. When
    it is missing/unparseable, return ``True`` for ``email_reply``/``email_compose``
    (never hide the banner on a parse error) and ``False`` otherwise. This is a UI
    hint only — the executor's force-Ask gate reads the stored annotation
    independently and remains the security truth (LUM-652).
    """
    if isinstance(annotation, dict) and "first_contact" in annotation:
        return bool(annotation.get("first_contact"))
    return action_name in _FIRST_CONTACT_SEND_ACTIONS


def get_proposal(proposal_id: int, *, user_id: str) -> dict | None:
    """A single owned proposal, or None. Includes ``annotation`` (LUM-652) so the
    create route can surface ``first_contact`` on the create response."""
    ms = config.get_metadata_store()
    return ms.fetch_one(
        "SELECT id, status, action_name, payload, annotation FROM action_proposals "
        "WHERE id = %s AND user_id = %s",
        (proposal_id, user_id),
    )
