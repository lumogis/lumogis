# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Approvals + connector-mode + elevation endpoints for the v1 façade.

Surface area (per plan §API routes → Approvals / audit):

* ``GET  /api/v1/approvals/pending``        — unioned pending list
* ``POST /api/v1/approvals/connector/{c}/mode`` — flip a connector's ASK/DO
* ``POST /api/v1/approvals/elevate``        — promote an action_type to routine

State-changing routes are gated by an in-process token-bucket
(``30/min/user``) modelled on :func:`routes.auth._rate_check`. Read-only
``GET /pending`` is unmetered.

Audit attribution writes go through :func:`actions.audit.write_audit`
with ``connector="__permissions_change__"`` so admin-shell filters can
discover them via ``action_type=set_mode``. The reserved prefix is
non-reversible (no ``reverse_token`` is minted) — toggling back is a
fresh ``set_connector_mode`` call which itself audits.

Which connectors may be switched to DO is decided in
:mod:`services.permission_connectors` (LUM-962, ADR 220): the connectors the
Ask/Do gate enforces — registered action connectors and known
``capability.{id}`` connectors — and no others. ``pending`` reports the same
verdict as ``suggested_action`` so a client never offers a switch that would
fail. A registry outage fails closed here (404), unlike ``PUT /me/permissions``
which degrade-allows a plain connector.

OpenAPI: do not add a docstring, ``summary=``, ``description=`` or ``responses=``
to ``pending``, ``set_mode`` or ``elevate`` — FastAPI copies them into the
OpenAPI document and ``make openapi-check`` would move. Explain in ``#`` comments.

CSRF: the state-changing routes carry no ``require_same_origin`` because Core's
API auth is bearer-only — a browser never auto-attaches a bearer, and the refresh
cookie is only accepted on ``/auth/*``. Add ``require_same_origin`` here if a
cookie-authenticated API session is ever introduced (the cross-device
cookie-session phase noted in ``csrf.py``).
"""

from __future__ import annotations

import json
import logging
import time
from collections import defaultdict
from collections import deque
from datetime import timedelta
from typing import Deque
from uuid import uuid4

from actions import audit as audit_module
from actions.executor import execute
from actions.executor import is_hard_limited
from actions.proposal_execute import claim_and_execute_proposal
from auth import get_user
from authz import require_user
from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException
from fastapi import Query
from fastapi import Request
from fastapi import status
from models.actions import AuditEntry
from models.api_v1 import ConnectorModeRequest
from models.api_v1 import ConnectorModeResponse
from models.api_v1 import DeniedActionItem
from models.api_v1 import ElevateRequest
from models.api_v1 import ElevateResponse
from models.api_v1 import ElevationCandidateItem
from models.api_v1 import PendingApprovalsResponse
from models.api_v1 import PendingProposalItem
from models.api_v1 import PendingProposalsResponse
from models.api_v1 import ProposalApproveResponse
from models.api_v1 import ProposalCancelResponse
from models.api_v1 import ProposalCreateRequest
from models.api_v1 import ProposalCreateResponse
from models.api_v1 import ProposalExecuteWireResponse
from models.api_v1 import RecoverableAttachments
from models.api_v1 import RecoverableSendItem
from models.api_v1 import RecoverableSendsResponse
from models.api_v1 import RecoveryDismissResponse
from models.api_v1 import RecoverySaveDraftResponse
from permissions import elevate_to_routine
from permissions import set_connector_mode
from pydantic import ValidationError
from services.api_v1_risk import elevation_eligible
from services.api_v1_risk import risk_tier_for

import config
from services import action_proposals
from services import permission_connectors
from services import send_outcome

_log = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/approvals",
    tags=["v1-approvals"],
    dependencies=[Depends(require_user)],
)


# ---------------------------------------------------------------------------
# In-process token-bucket: 30 state-changing approvals calls / 60s / user.
# Mirrors the shape of `routes/auth.py::_rate_check` so the operational
# story is identical (per-process; lost on restart; no Redis).
# ---------------------------------------------------------------------------

_APPROVAL_WINDOW_SEC = 60.0
_APPROVAL_LIMIT = 30
_approval_calls: dict[str, Deque[float]] = defaultdict(deque)


def _approvals_rate_check(request: Request) -> None:
    user_id = get_user(request).user_id
    now = time.monotonic()
    bucket = _approval_calls[user_id]
    while bucket and now - bucket[0] > _APPROVAL_WINDOW_SEC:
        bucket.popleft()
    if len(bucket) >= _APPROVAL_LIMIT:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="too many approval changes; try again in a minute",
            headers={"Retry-After": "60"},
        )
    bucket.append(now)


# ---------------------------------------------------------------------------
# GET /api/v1/approvals/pending
# ---------------------------------------------------------------------------


@router.get("/pending", response_model=PendingApprovalsResponse)
def pending(
    request: Request,
    limit: int = Query(50, ge=1, le=200),
    as_user: str | None = Query(None),
) -> PendingApprovalsResponse:
    caller = get_user(request)
    target_user_id = _resolve_as_user(caller, as_user)

    ms = config.get_metadata_store()

    denied_rows = _safe_fetch_all(
        ms,
        "SELECT id, connector, action_type, input_summary, created_at "
        "FROM action_log "
        "WHERE allowed = FALSE AND user_id = %s "
        "ORDER BY created_at DESC LIMIT %s",
        (target_user_id, limit),
    )

    # SCOPE-EXEMPT: routine_do_tracking has no `scope` column (per-user
    # routine elevation tracking — migration 016).
    elev_rows = _safe_fetch_all(
        ms,
        "SELECT connector, action_type, approval_count "
        "FROM routine_do_tracking "
        # SCOPE-EXEMPT: routine_do_tracking is per-user, no scope column.
        "WHERE user_id = %s AND approval_count >= 15 "
        "      AND auto_approved = FALSE AND edit_count = 0 "
        "ORDER BY approval_count DESC LIMIT %s",
        (target_user_id, limit),
    )

    # One registry read per request (pairs) and per distinct denied connector
    # (gate); both registries are in-memory. ``None`` = registry unavailable, and
    # every use below reads that as "refuse" (rows become explain_only).
    action_pairs = permission_connectors.registered_action_pairs() or set()
    gates = {
        c: permission_connectors.connector_gate(c) for c in {r["connector"] for r in denied_rows}
    }

    pending: list = []
    for r in denied_rows:
        action_type = r["action_type"]
        # Eligible only when the pair is a registered action: a capability tool is
        # never in the action registry, so `elevate` would 404 for it.
        eligible = elevation_eligible(action_type) and (r["connector"], action_type) in action_pairs
        suggested = _suggested_action(
            ms,
            target_user_id=target_user_id,
            connector=r["connector"],
            action_type=action_type,
            gate=gates[r["connector"]],
            action_pairs=action_pairs,
        )
        pending.append(
            DeniedActionItem(
                action_log_id=r["id"],
                connector=r["connector"],
                action_type=action_type,
                risk_tier=risk_tier_for(action_type),
                input_summary=r.get("input_summary"),
                occurred_at=r["created_at"],
                elevation_eligible=eligible,
                suggested_action=suggested,
            )
        )

    for r in elev_rows:
        action_type = r["action_type"]
        pending.append(
            ElevationCandidateItem(
                connector=r["connector"],
                action_type=action_type,
                approval_count=int(r["approval_count"]),
                risk_tier=risk_tier_for(action_type),
                elevation_eligible=(
                    elevation_eligible(action_type)
                    and (r["connector"], action_type) in action_pairs
                ),
            )
        )

    return PendingApprovalsResponse(pending=pending[:limit])


def _suggested_action(
    ms,
    *,
    target_user_id: str,
    connector: str,
    action_type: str,
    gate: permission_connectors.ConnectorGate | None,
    action_pairs: set[tuple[str, str]],
) -> str:
    """Decision tree from plan §Data contracts → DeniedActionItem."""
    if is_hard_limited(action_type):
        return "explain_only"
    # The switch would 404: unknown, stale, tool-only, or registry unavailable.
    if gate is None or not gate.settable:
        return "explain_only"
    # The switch would 403 (same rule `set_mode` applies).
    if permission_connectors.do_refused(gate.gated_action_types, is_hard_limited=is_hard_limited):
        return "explain_only"
    # SCOPE-EXEMPT: routine_do_tracking is per-user, no scope column.
    row = _safe_fetch_one(
        ms,
        "SELECT approval_count, auto_approved, edit_count "
        "FROM routine_do_tracking "
        "WHERE user_id = %s AND connector = %s AND action_type = %s",
        (target_user_id, connector, action_type),
    )
    if (
        row is not None
        and int(row.get("approval_count") or 0) >= 15
        and not row.get("auto_approved")
        and int(row.get("edit_count") or 0) == 0
        and (connector, action_type) in action_pairs
    ):
        return "elevate_action_type"
    return "set_connector_do"


def _resolve_as_user(caller, as_user: str | None) -> str:
    """Apply admin-only ``?as_user=...`` rules.

    Plan §API routes → Approvals: non-admin requests with ``as_user`` set
    are rejected with 403 to avoid silent self-scoping that would mask
    privilege errors.
    """
    if as_user is None:
        return caller.user_id
    if caller.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": "admin_required"},
        )
    return as_user


# ---------------------------------------------------------------------------
# POST /api/v1/approvals/connector/{connector}/mode
# ---------------------------------------------------------------------------


@router.post(
    "/connector/{connector}/mode",
    response_model=ConnectorModeResponse,
    dependencies=[Depends(_approvals_rate_check)],
)
def set_mode(
    connector: str,
    body: ConnectorModeRequest,
    request: Request,
) -> ConnectorModeResponse:
    caller = get_user(request)

    # One registry snapshot decides both the 404 and the 403 (no second read).
    # `None` = the relevant registry raised: fail closed. `connector` is an
    # unconstrained path param, so it is logged with %r (no forged log lines).
    gate = permission_connectors.connector_gate(connector)
    if gate is None:
        _log.warning("approvals_set_mode_registry_unavailable connector=%r", connector)
    if gate is None or not gate.settable:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "unknown_connector"},
        )

    # DO skips the hard-limit test in `check_permission`, so a connector with ANY
    # hard-limited gated type must not be flipped from here.
    if permission_connectors.do_refused(gate.gated_action_types, is_hard_limited=is_hard_limited):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": "hard_limited_connector"},
        )

    try:
        set_connector_mode(user_id=caller.user_id, connector=connector, mode=body.mode)
    except ValueError as exc:  # invalid mode — Pydantic already gates,
        # but defence-in-depth keeps the contract honest.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "invalid_mode", "detail": str(exc)},
        ) from exc
    except Exception as exc:  # noqa: BLE001
        _log.exception(
            "approvals.set_mode failed user=%s connector=%s mode=%s",
            caller.user_id,
            connector,
            body.mode,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "internal_error"},
        ) from exc

    audit_module.write_audit(
        AuditEntry(
            action_name=f"__permissions_change__.{connector}",
            connector="__permissions_change__",
            mode="ask",
            input_summary=json.dumps({"new_mode": body.mode, "changed_by": caller.user_id}),
            result_summary=json.dumps({"ok": True}),
            user_id=caller.user_id,
        )
    )

    return ConnectorModeResponse(connector=connector, mode=body.mode)


# ---------------------------------------------------------------------------
# POST /api/v1/approvals/elevate
# ---------------------------------------------------------------------------


@router.post(
    "/elevate",
    response_model=ElevateResponse,
    dependencies=[Depends(_approvals_rate_check)],
)
def elevate(body: ElevateRequest, request: Request) -> ElevateResponse:
    caller = get_user(request)

    if is_hard_limited(body.action_type):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": "hard_limited_action"},
        )

    pairs = permission_connectors.registered_action_pairs()
    if pairs is None:
        _log.warning(
            "approvals_elevate_registry_unavailable connector=%r action_type=%r",
            body.connector,
            body.action_type,
        )
    # Fail closed on an unreadable registry (was an unhandled 500).
    if pairs is None or (body.connector, body.action_type) not in pairs:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "unknown_action"},
        )

    try:
        elevate_to_routine(
            user_id=caller.user_id,
            connector=body.connector,
            action_type=body.action_type,
        )
    except Exception as exc:  # noqa: BLE001
        _log.exception(
            "approvals.elevate failed user=%s connector=%s action_type=%s",
            caller.user_id,
            body.connector,
            body.action_type,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "internal_error"},
        ) from exc

    return ElevateResponse(connector=body.connector, action_type=body.action_type)


# ---------------------------------------------------------------------------
# POST /api/v1/approvals/proposals/{proposal_id}/execute
# ---------------------------------------------------------------------------


@router.post(
    "/proposals/{proposal_id}/execute",
    response_model=ProposalExecuteWireResponse,
    dependencies=[Depends(_approvals_rate_check)],
)
def execute_approved_proposal(
    proposal_id: int,
    request: Request,
    as_user: str | None = Query(None),
) -> ProposalExecuteWireResponse:
    """Claim one **approved** proposal row and run :func:`actions.executor.execute`."""
    caller = get_user(request)
    target_user_id = _resolve_as_user(caller, as_user)
    worker_id = f"http-{uuid4()}"
    result = claim_and_execute_proposal(
        proposal_id,
        worker_id=worker_id,
        user_id=target_user_id,
    )
    return ProposalExecuteWireResponse(
        success=result.success,
        output=result.output,
        error=result.error,
        reverse_token=result.reverse_token,
        code=result.code,
    )


# ---------------------------------------------------------------------------
# Proposal producer — create / approve / list (Ask/Do enqueue half, LUM-623)
# ---------------------------------------------------------------------------


@router.post(
    "/proposals",
    response_model=ProposalCreateResponse,
    status_code=201,
    dependencies=[Depends(_approvals_rate_check)],
)
def create_proposal_route(
    body: ProposalCreateRequest,
    request: Request,
) -> ProposalCreateResponse:
    """Enqueue a ``pending`` Ask/Do proposal for a registered action.

    ``payload`` is validated against the action's ``input_model`` server-side
    (per-action, ``extra="forbid"``); a smuggled ``user_id`` is a 422. Identity
    is the authenticated caller — never anything in the payload. ``as_user`` is
    intentionally not supported here in v1.
    """
    caller = get_user(request)
    proposal_id = action_proposals.create_proposal(
        body.action_name, body.payload, user_id=caller.user_id
    )
    # Read back the stored annotation (owner-scoped) so the confirm card can badge
    # a first-contact send immediately, before any list refetch (LUM-652). The
    # display flag fails closed for send actions.
    stored = action_proposals.get_proposal(proposal_id, user_id=caller.user_id)
    annotation = (stored or {}).get("annotation")
    if isinstance(annotation, str):
        annotation = json.loads(annotation)
    first_contact = action_proposals.first_contact_for_display(
        body.action_name, annotation if isinstance(annotation, dict) else None
    )
    return ProposalCreateResponse(
        proposal_id=proposal_id, status="pending", first_contact=first_contact
    )


@router.post(
    "/proposals/{proposal_id}/approve",
    response_model=ProposalApproveResponse,
    dependencies=[Depends(_approvals_rate_check)],
)
def approve_proposal_route(
    proposal_id: int,
    request: Request,
) -> ProposalApproveResponse:
    """Owner-guarded ``pending → approved`` transition (404 for other users)."""
    caller = get_user(request)
    out = action_proposals.approve_proposal(proposal_id, user_id=caller.user_id)
    return ProposalApproveResponse(proposal_id=out["proposal_id"], status=out["status"])


@router.post(
    "/proposals/{proposal_id}/cancel",
    response_model=ProposalCancelResponse,
    dependencies=[Depends(_approvals_rate_check)],
)
def cancel_proposal_route(
    proposal_id: int,
    request: Request,
) -> ProposalCancelResponse:
    """Owner-guarded ``pending → rejected`` transition — a user abandoning an unsent
    proposal (the send composer's Save-to-Drafts / close path; 404 for other users or
    a non-pending row) (LUM-682)."""
    caller = get_user(request)
    out = action_proposals.cancel_proposal(proposal_id, user_id=caller.user_id)
    return ProposalCancelResponse(proposal_id=out["proposal_id"], status=out["status"])


@router.get("/proposals", response_model=PendingProposalsResponse)
def list_proposals_route(request: Request) -> PendingProposalsResponse:
    """List the caller's own ``pending`` proposals (the existing
    ``GET /pending`` returns ``action_log`` denials, not proposals)."""
    caller = get_user(request)
    rows = action_proposals.list_pending(caller.user_id)
    items: list[PendingProposalItem] = []
    for r in rows:
        first_contact = action_proposals.first_contact_for_display(
            str(r.get("action_name", "")), r.get("annotation")
        )
        items.append(
            PendingProposalItem(
                proposal_id=r["proposal_id"],
                action_name=r["action_name"],
                payload=r.get("payload") or {},
                status=r["status"],
                created_at=r.get("created_at"),
                first_contact=first_contact,
            )
        )
    return PendingProposalsResponse(proposals=items)


# ---------------------------------------------------------------------------
# Failed-send recovery (LUM-793)
#
# A read surface over MESSAGE BODIES, which makes it the most sensitive listing
# in the façade. Every query below filters `user_id`, and cross-user isolation is
# proven in the real-Postgres lane rather than the fake — `FakeProposalQueueStore`
# pattern-matches SQL strings and re-implements the logic in Python, so a dropped
# predicate here would still pass a fake-lane test (demonstrated in LUM-762).
# ---------------------------------------------------------------------------


def _recovery_attachments(proposal_id: int, *, user_id: str, finished_at) -> RecoverableAttachments:
    """Attachment availability, read from staging and never from the payload."""
    try:
        from services import email_attachment_staging as staging

        files = staging.available_attachments(proposal_id, user_id=user_id)
        expires_at = None
        if files and finished_at is not None:
            expires_at = finished_at + timedelta(hours=staging.dead_grace_hours())
        return RecoverableAttachments(
            count=len(files), available=bool(files), expires_at=expires_at
        )
    except Exception as exc:  # noqa: BLE001 - availability is a hint, not the feature
        _log.warning("approvals: attachment availability failed pid=%s: %s", proposal_id, exc)
        return RecoverableAttachments(count=0, available=False)


@router.get("/recoverable-sends", response_model=RecoverableSendsResponse)
def list_recoverable_sends_route(request: Request) -> RecoverableSendsResponse:
    """The caller's failed sends, with their body and whether they actually went out."""
    caller = get_user(request)
    rows = action_proposals.list_recoverable_sends(caller.user_id)
    items: list[RecoverableSendItem] = []
    for r in rows:
        payload = r.get("payload") or {}
        outcome = send_outcome.resolve_send_outcome(r, user_id=caller.user_id)
        items.append(
            RecoverableSendItem(
                proposal_id=r["proposal_id"],
                action_name=r["action_name"],
                to=[str(a) for a in (payload.get("to") or [])],
                subject=str(payload.get("subject") or ""),
                body=str(payload.get("body") or ""),
                failed_at=r.get("finished_at"),
                error=r.get("error"),
                outcome=outcome.state,
                sent_at=outcome.sent_at,
                delivered_to=outcome.delivered_to,
                refused=outcome.refused,
                attachments=_recovery_attachments(
                    r["proposal_id"], user_id=caller.user_id, finished_at=r.get("finished_at")
                ),
            )
        )
    return RecoverableSendsResponse(items=items)


def _load_recoverable(proposal_id: int, *, user_id: str) -> dict:
    """One owned, still-listed failed send — or 404 with no oracle.

    Re-reads the listing rather than `get_proposal` so the action-set and window
    rules are enforced identically on the write paths: a dead `email_unsubscribe`
    must be no more saveable-to-Drafts than it is listable.
    """
    for row in action_proposals.list_recoverable_sends(user_id):
        if int(row["proposal_id"]) == int(proposal_id):
            return row
    raise HTTPException(status.HTTP_404_NOT_FOUND, detail={"error": "not_found"})


@router.post(
    "/recoverable-sends/{proposal_id}/save-draft",
    response_model=RecoverySaveDraftResponse,
    dependencies=[Depends(_approvals_rate_check)],
)
def save_recoverable_send_to_drafts(
    proposal_id: int,
    request: Request,
) -> RecoverySaveDraftResponse:
    """Write the stored message to the mailbox's Drafts folder via ``email_save_draft``.

    Runs a **registered action through the executor**, so audit, permission and
    reversal wiring are inherited rather than re-implemented here.
    """
    caller = get_user(request)
    row = _load_recoverable(proposal_id, user_id=caller.user_id)

    # Re-resolve server-side. The listing is a page a household member can leave
    # open for hours; by the time they click, the row may have been resolved as
    # sent. Hiding the button client-side is not a guard on a server-side effect.
    # `partially_sent` deliberately still saves — those recipients did NOT get it.
    outcome = send_outcome.resolve_send_outcome(row, user_id=caller.user_id)
    if outcome.is_sent:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={
                "error": "already_sent",
                "proposal_id": proposal_id,
                "sent_at": outcome.sent_at.isoformat() if outcome.sent_at else None,
            },
        )

    draft_input, dropped = _draft_input_from_send_payload(
        row.get("payload") or {}, proposal_id=proposal_id, user_id=caller.user_id
    )

    # `approved=True` — the Ask-tier gate, satisfied. Worth writing down because
    # it asserts "a human approved this", and a reviewer should be able to check
    # the claim rather than trust it:
    #
    # * The row is `dead`, and `dead` is reachable ONLY from `executing`
    #   (`_DEAD_LETTER_SQL` / `_RESET_STUCK_DEAD_SQL` both require it), which is
    #   reachable only from `approved` via `claim_by_id`, which is reachable only
    #   from `pending` via the owner-guarded `approve_proposal`. So every row that
    #   can reach this line was explicitly approved by its owner.
    # * The approval was for a SEND. This runs `email_save_draft` instead —
    #   strictly weaker: IMAP-only, no SMTP opt-in, From is the mailbox's own
    #   account, and it contacts nobody. Same message, smaller consequence.
    # * The click itself is the human in the loop; it is the same action the
    #   composer already offers as its failure fallback.
    #
    # `check_permission` still runs and still refuses hard-limited action types
    # under `approved=True`, and `email_save_draft` is not one. Identity is the
    # authenticated caller — never anything in the stored payload — and the
    # handler re-checks source ownership at execute time (`_load_owned_source`),
    # so a payload tampered with directly in the database cannot reach another
    # member's mailbox.
    result = execute(
        "email_save_draft",
        draft_input,
        user_id=caller.user_id,
        approved=True,
    )
    # A failed save leaves the row LISTED — not dismissed, not archived. Being
    # able to try again is the entire feature.
    return RecoverySaveDraftResponse(
        proposal_id=proposal_id,
        success=bool(result.success),
        error=result.error,
        code=result.code,
        attachments_dropped=dropped,
    )


def _draft_input_from_send_payload(
    payload: dict, *, proposal_id: int, user_id: str
) -> tuple[dict, int]:
    """Turn a stored send payload into a valid ``email_save_draft`` input.

    No field mapping is required: ``EmailSaveDraftInput`` is a strict superset of
    all three send inputs (reply, compose, forward), so ``extra="forbid"`` cannot
    fire on a payload that was itself validated at propose time. What IS required
    is dropping staged attachment ids whose files the sweep has taken, and
    asserting the result satisfies one of the three draft SHAPES rather than
    assuming it — an assumption is how a 422 becomes a dead-lettered proposal.
    """
    from actions.handlers.smtp_send import EmailSaveDraftInput

    from services import email_attachment_staging as staging

    out = dict(payload)
    dropped = 0
    wanted = [str(i) for i in (out.get("attachment_ids") or []) if i]
    if wanted:
        try:
            live = {f.id for f in staging.available_attachments(proposal_id, user_id=user_id)}
        except Exception as exc:  # noqa: BLE001
            # Can't check → drop NOTHING. Dropping on an unreadable staging table
            # would silently strip files that are still perfectly present, and
            # the user would never know which. Keeping them costs at worst a
            # loud `PermanentActionError` from the handler (surfaced as
            # `success: false`, row stays listed, they retry) — a visible
            # failure beats a silent, wrong success.
            _log.warning("approvals: staging lookup failed pid=%s: %s", proposal_id, exc)
            live = set(wanted)
        kept = [i for i in wanted if i in live]
        dropped = len(wanted) - len(kept)
        # A draft is NOT a send. `_resolve_attachment_parts` refuses a send that
        # lost a file because a send is unrecallable and the user approved a
        # message they believed carried it. A draft is recoverable by definition:
        # the user opens it, sees what is there, and re-attaches. Refusing to
        # save it leaves them with nothing, which is strictly worse than a draft
        # plus an honest sentence — so strip and report.
        out["attachment_ids"] = kept

    try:
        EmailSaveDraftInput.model_validate(out)
    except ValidationError as exc:
        # Log the SHAPE of the failure, never the values. `str(ValidationError)`
        # embeds `input_value=...`, so logging the exception verbatim writes the
        # message body and recipient addresses of a private email into the
        # orchestrator log — a household member's correspondence landing
        # somewhere with different read permissions than the row it came from.
        # The field paths and error types are what a diagnosis actually needs.
        _log.warning(
            "approvals: recovery payload is not a valid draft shape pid=%s errors=%s",
            proposal_id,
            [
                {"loc": ".".join(str(p) for p in e.get("loc", ())), "type": e.get("type")}
                for e in exc.errors()
            ],
        )
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": "unsaveable_payload", "proposal_id": proposal_id},
        ) from None
    return out, dropped


@router.post(
    "/recoverable-sends/{proposal_id}/dismiss",
    response_model=RecoveryDismissResponse,
    dependencies=[Depends(_approvals_rate_check)],
)
def dismiss_recoverable_send_route(
    proposal_id: int,
    request: Request,
) -> RecoveryDismissResponse:
    """Owner-guarded ``dead → archived`` — stops the listing, deletes nothing."""
    caller = get_user(request)
    out = action_proposals.dismiss_recoverable_send(proposal_id, user_id=caller.user_id)
    return RecoveryDismissResponse(proposal_id=out["proposal_id"], status=out["status"])


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _safe_fetch_all(ms, sql: str, params) -> list[dict]:
    try:
        return list(ms.fetch_all(sql, params) or [])
    except Exception as exc:  # noqa: BLE001
        _log.warning("approvals: fetch_all failed: %s", exc)
        return []


def _safe_fetch_one(ms, sql: str, params) -> dict | None:
    try:
        return ms.fetch_one(sql, params)
    except Exception as exc:  # noqa: BLE001
        _log.warning("approvals: fetch_one failed: %s", exc)
        return None
