# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Opt-in, filed list, and undo for attachment auto-ingest (LUM-702).

Ownership answers **404**, never 403, throughout — a 403 would confirm that
another user's mailbox or ledger row exists, turning the gate into an oracle over
someone else's mail. That is the posture the whole email family holds.

Enabling on a **metadata-mode** mailbox is a **422**, not a silent success. Such a
mailbox never fetched bodies, so ``email_attachment`` has no rows for it and the
toggle would do nothing at all — which the user would reasonably read as the
feature being broken. The web disables the control with an explanation first; this
is the backstop, and it names full-ingest so the message is actionable.
"""

from __future__ import annotations

import logging
import os
from typing import Literal
from typing import Optional

from auth import UserContext
from authz import require_user
from csrf import require_same_origin
from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException
from fastapi import Query
from fastapi import Request
from fastapi import Response
from fastapi import status
from pydantic import BaseModel
from pydantic import Field
from pydantic import model_validator
from rate_limit import RequestRateLimiter

import config
from services import email_attachment_ingest as ingest_svc
from services import email_attachment_rules as rules_svc
from services import paperless_overlap as overlap_svc

_log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/email", tags=["email-attachment-ingest"])

_MAX_FILED_ROWS = 200
_MAX_CANDIDATES = int(os.environ.get("EMAIL_ATTACHMENT_CANDIDATES_MAX", "200"))
# A bulk accept of N files is N fetch+extract+embed cycles ON THE REQUEST
# PATH, so it is bounded and reports what it did not reach rather than
# truncating silently.
_MAX_ACCEPT_PER_CALL = int(os.environ.get("EMAIL_ATTACHMENT_ACCEPT_MAX_PER_CALL", "25"))


class AttachmentIngestSettings(BaseModel):
    enabled: bool
    #: Moves the watermark back to epoch, widening the sweep over existing mail.
    apply_to_existing: bool = False


class FiledAttachmentRow(BaseModel):
    id: int
    signal_id: str
    part_index: int
    filename: str
    mime_type: str
    size_bytes: int
    logical_path: str
    ingested_at: str
    #: Sender and subject of the email this came from. Empty when the signal
    #: has since been purged — the ledger keeps no FK on signal_id (066/067).
    from_addr: str = ""
    from_display: str = ""
    subject: str = ""
    reversed_at: Optional[str] = None
    skipped_reason: Optional[str] = None
    #: LUM-742. The mailbox this was filed from. **No `source_name` here**, unlike
    #: `AttachmentCandidate`: `filed_rows` joins only `signals`, so a name would
    #: cost a `sources` join added purely for a label — and the page already holds
    #: the sources listing for its activation branch, so the client resolves it.
    source_id: str = ""


class FiledAttachmentsResponse(BaseModel):
    rows: list[FiledAttachmentRow]
    total: int


class UndoRequest(BaseModel):
    log_id: Optional[int] = Field(default=None)
    source_id: Optional[str] = Field(default=None)

    @model_validator(mode="after")
    def _exactly_one(self) -> "UndoRequest":
        if (self.log_id is None) == (self.source_id is None):
            raise ValueError("provide exactly one of log_id or source_id")
        return self


class UndoResponse(BaseModel):
    reversed_count: int


class RefileRequest(BaseModel):
    log_id: int


class RefileResponse(BaseModel):
    #: `refiled` | `not_found` | `duplicate` | `unavailable`
    outcome: str


def _owned_imap_source(user_id: str, source_id: str) -> dict:
    """404 unless ``source_id`` is an IMAP mailbox owned by ``user_id``."""
    ms = config.get_metadata_store()
    row = ms.fetch_one(
        "SELECT id::text AS id, extraction_method FROM sources "
        "WHERE id::text = %s AND user_id = %s AND source_type = 'imap'",
        (str(source_id), user_id),
    )
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail={"error": "not_found"})
    return dict(row)


def _iso(value) -> str:
    if value is None:
        return ""
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


@router.get("/attachment-ingest/filed", response_model=FiledAttachmentsResponse)
def list_filed_attachments(
    source_id: Optional[str] = Query(default=None),
    limit: int = Query(default=100, ge=1, le=_MAX_FILED_ROWS),
    include_declined: bool = Query(default=False),
    caller: UserContext = Depends(require_user),
) -> FiledAttachmentsResponse:
    """Filings and skips alike — the skips are how the UI explains an absence.

    **Declines are excluded by default** (LUM-727). They are a skip reason like
    any other, but a user declining most of what arrives would otherwise turn
    this into a list of things they said no to.
    """
    if source_id:
        _owned_imap_source(caller.user_id, source_id)
    rows = ingest_svc.filed_rows(
        user_id=caller.user_id,
        source_id=source_id,
        limit=int(limit),
        include_declined=bool(include_declined),
    )
    return FiledAttachmentsResponse(
        rows=[
            FiledAttachmentRow(
                id=int(r["id"]),
                signal_id=str(r["signal_id"]),
                part_index=int(r["part_index"]),
                filename=str(r["filename"] or ""),
                mime_type=str(r["mime_type"] or ""),
                size_bytes=int(r["size_bytes"] or 0),
                logical_path=str(r["logical_path"] or ""),
                ingested_at=_iso(r.get("ingested_at")),
                from_addr=str(r.get("from_addr") or ""),
                from_display=str(r.get("from_display") or ""),
                subject=str(r.get("title") or ""),
                reversed_at=_iso(r.get("reversed_at")) or None,
                skipped_reason=r.get("skipped_reason") or None,
                source_id=str(r.get("source_id") or ""),
            )
            for r in rows
        ],
        total=len(rows),
    )


@router.post(
    "/attachment-ingest/undo",
    response_model=UndoResponse,
    dependencies=[Depends(require_same_origin)],
)
def undo_attachment_ingest(
    body: UndoRequest,
    caller: UserContext = Depends(require_user),
) -> UndoResponse:
    """Take a filing back — one row, or a whole mailbox's worth.

    The count reflects what actually landed: a row whose vector delete failed is
    excluded and left un-reversed, so the next attempt retries rather than
    reporting a document gone that is still searchable.
    """
    if body.source_id is not None:
        _owned_imap_source(caller.user_id, body.source_id)
        return UndoResponse(
            reversed_count=ingest_svc.undo_for_source(caller.user_id, body.source_id)
        )

    ok = ingest_svc.undo_row(caller.user_id, int(body.log_id))
    # Not found, another user's row, and already-reversed are indistinguishable
    # on purpose — and all three are honestly "nothing was reversed".
    return UndoResponse(reversed_count=1 if ok else 0)


@router.patch(
    "/sources/{source_id}/attachment-ingest",
    dependencies=[Depends(require_same_origin)],
)
def set_attachment_ingest(
    source_id: str,
    body: AttachmentIngestSettings,
    caller: UserContext = Depends(require_user),
) -> dict:
    source = _owned_imap_source(caller.user_id, source_id)

    if str(source.get("extraction_method") or "") != "imap_full_ingest":
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error": "full_ingest_required",
                "message": (
                    "This mailbox is in metadata mode, so its message bodies — and "
                    "therefore its attachments — were never fetched. Switch it to "
                    "full ingest before enabling attachment filing."
                ),
            },
        )

    ms = config.get_metadata_store()
    # NOW() on first enable (go-forward only); epoch when applying to existing
    # mail, which widens the standing sweep over history. Same semantics as
    # organize_since (LUM-645), and disabling leaves the watermark alone so
    # re-enabling does not silently re-sweep everything since the first opt-in.
    since_sql = (
        "'epoch'::timestamptz"
        if body.apply_to_existing
        else "COALESCE(attachment_ingest_since, NOW())"
    )
    ms.execute(
        "UPDATE sources SET attachment_ingest_enabled = %s, "
        f"attachment_ingest_since = CASE WHEN %s THEN {since_sql} "
        "ELSE attachment_ingest_since END "
        "WHERE id::text = %s AND user_id = %s",
        (body.enabled, body.enabled, str(source_id), caller.user_id),
    )
    return {"updated": True, "apply_to_existing": body.apply_to_existing}


@router.post(
    "/attachment-ingest/refile",
    response_model=RefileResponse,
    dependencies=[Depends(require_same_origin)],
)
def refile_attachment(
    body: RefileRequest,
    caller: UserContext = Depends(require_user),
) -> RefileResponse:
    """Put back a filing the user previously undid (LUM-721).

    Undo is deliberately sticky — the sweep never reconsiders a part it has
    judged, which is what stops an undo reverting itself on the next tick. This is
    the explicit way back.

    Always **200**, never a 4xx for the not-found case: "not your row", "never
    filed" and "not currently reversed" are indistinguishable on purpose, so this
    cannot be used to probe another user's ledger. The outcome string says what
    happened.
    """
    outcome = ingest_svc.refile_row(caller.user_id, int(body.log_id))
    return RefileResponse(outcome=outcome)


# --------------------------------------------------------------------------- #
# Candidates — the per-attachment decision (LUM-727)
# --------------------------------------------------------------------------- #


class SourceRef(BaseModel):
    """A mailbox named in a response for a reason the user can act on."""

    source_id: str
    source_name: str = ""


class AttachmentCandidate(BaseModel):
    signal_id: str
    part_index: int
    filename: str
    mime_type: str
    size_bytes: int
    from_addr: str = ""
    from_display: str = ""
    subject: str = ""
    category: str = ""
    received_at: str = ""
    #: LUM-742. Defaulted so an older client deserialises unchanged.
    source_id: str = ""
    source_name: str = ""


class CandidateGroup(BaseModel):
    """Grouped by SENDER **and SOURCE** (LUM-742).

    Grouping is **presentation over a flat list** — the query selects rows and
    this shapes them — so if the real distribution turns out long-tailed rather
    than concentrated, the cost is a UI change and not a query revision.

    Source is half the key because "Always file from this sender" creates a rule,
    and a rule belongs to exactly one mailbox. A group spanning two mailboxes has
    no rule the user could mean, which is why the button used to disappear on any
    unscoped list — and the LUM-728 paperless gate with it.
    """

    from_addr: str
    from_display: str
    source_id: str = ""
    source_name: str = ""
    count: int
    candidates: list[AttachmentCandidate]


class CandidatesResponse(BaseModel):
    groups: list[CandidateGroup]
    #: Page length AFTER trimming — **not** a pending count. It can never exceed
    #: `limit`, so anything wanting "how many are waiting?" reads `pending_total`.
    total: int
    #: LUM-742: every pending attachment across the caller's mailboxes, unbounded.
    pending_total: int = 0
    truncated: bool
    #: Attachments the mailbox can no longer produce (`message_gone`). Terminal
    #: by design — but terminal must not mean invisible, or a user waiting to
    #: file an invoice watches it never appear and concludes this is broken.
    unreachable: int = 0
    #: LUM-742: mailboxes with rows beyond their quota. The whole user-facing
    #: mitigation for a mailbox not being fully represented — without it, "your
    #: window filled up" is indistinguishable from "nothing pending here".
    truncated_sources: list[SourceRef] = []
    #: Mailboxes whose query failed this request. Reported rather than swallowed,
    #: for the same reason: a silently-absent mailbox reads as a broken feature.
    errored_sources: list[SourceRef] = []


class CandidateRef(BaseModel):
    signal_id: str
    part_index: int


class BulkCandidateRequest(BaseModel):
    refs: list[CandidateRef] = Field(default_factory=list, max_length=200)


class AcceptResponse(BaseModel):
    accepted: int
    #: What was not filed, and why — never silent truncation.
    skipped: list[dict]


class DeclineResponse(BaseModel):
    declined: int


class UndoDeclineRequest(BaseModel):
    signal_id: str
    part_index: int


class AttachmentRuleModel(BaseModel):
    id: str
    match_type: str
    match_value: str
    created_at: str
    #: True when a category rule names a slug that no longer exists — the rule is
    #: inert and must be shown as such, not listed as if it were working.
    stale: bool = False


class CreateRuleRequest(BaseModel):
    source_id: str
    match_type: str
    match_value: str
    # LUM-728 — the consent gate's answer. Mirrors LUM-729's
    # `acknowledge_external_consumer`: the refusal is a question, and this field
    # is how the re-sent request answers it.
    acknowledge_paperless_overlap: bool = False
    # LUM-743 — WHICH mailbox that answer was about.
    #
    # The boolean alone cannot say. That was harmless while the review list was
    # one mailbox at a time, because "acknowledged" and "acknowledged for this
    # mailbox" were the same statement. LUM-742 made the list span mailboxes,
    # and the only thing then binding a consent to a mailbox was a client-side
    # `Set` — so a regression there would have sent `true` for a rule on a
    # mailbox the user never answered about, silently skipping the probe.
    #
    # The server can now check the claim instead of trusting it. Absent or
    # mismatched means "not acknowledged", so the gate runs: **fail closed**.
    acknowledged_source_id: Optional[str] = None


@router.get("/attachment-ingest/candidates", response_model=CandidatesResponse)
def list_candidates(
    source_id: Optional[str] = Query(default=None),
    limit: int = Query(default=100, ge=1, le=_MAX_CANDIDATES),
    caller: UserContext = Depends(require_user),
) -> CandidatesResponse:
    """Attachments awaiting a decision. **Opens no IMAP session.**

    Everything here is already stored — `email_attachment` has the file metadata
    and `signals` the sender and category — so the attachments a user declines
    cost nothing beyond rows that already existed.
    """
    if source_id:
        _owned_imap_source(caller.user_id, source_id)
    # LUM-742: paging, fair-share and truncation all live in the service now. The
    # old `limit + 1` fetch and `len(rows) > limit` trim here would be a second,
    # competing truncation mechanism on top of the per-source one.
    page = ingest_svc.candidates_page_for_user(
        user_id=caller.user_id, source_id=source_id, limit=int(limit)
    )
    rows = page.candidates

    # Key is (sender, source): a rule belongs to exactly one mailbox, so a group
    # spanning two would have no rule "Always file from this sender" could make.
    grouped: dict[tuple[str, str], list] = {}
    for c in rows:
        grouped.setdefault((c.from_addr or "", c.source_id or ""), []).append(c)
    groups = [
        CandidateGroup(
            from_addr=addr,
            from_display=(items[0].from_display or addr),
            source_id=sid,
            source_name=(items[0].source_name or ""),
            count=len(items),
            candidates=[AttachmentCandidate(**vars(c)) for c in items],
        )
        for (addr, sid), items in grouped.items()
    ]
    # `source_name` breaks the tie so two mailboxes' equal-sized groups for the
    # same sender order deterministically rather than by dict insertion.
    groups.sort(key=lambda g: (-g.count, g.from_display, g.source_name))
    return CandidatesResponse(
        groups=groups,
        total=len(rows),
        pending_total=page.pending_total,
        truncated=bool(page.truncated_sources),
        unreachable=ingest_svc.unreachable_count(user_id=caller.user_id, source_id=source_id),
        truncated_sources=[
            SourceRef(source_id=i, source_name=n) for i, n in page.truncated_sources
        ],
        errored_sources=[SourceRef(source_id=i, source_name=n) for i, n in page.errored_sources],
    )


@router.post(
    "/attachment-ingest/candidates/accept",
    response_model=AcceptResponse,
    dependencies=[Depends(require_same_origin)],
)
def accept_candidates(
    body: BulkCandidateRequest,
    caller: UserContext = Depends(require_user),
) -> AcceptResponse:
    """File the picked attachments. The **only** path that spends IMAP/embedding."""
    out = ingest_svc.accept_candidates(
        caller.user_id,
        [(r.signal_id, r.part_index) for r in body.refs],
        limit=_MAX_ACCEPT_PER_CALL,
    )
    return AcceptResponse(accepted=int(out["accepted"]), skipped=list(out["skipped"]))


@router.post(
    "/attachment-ingest/candidates/decline",
    response_model=DeclineResponse,
    dependencies=[Depends(require_same_origin)],
)
def decline_candidates(
    body: BulkCandidateRequest,
    caller: UserContext = Depends(require_user),
) -> DeclineResponse:
    """Say no, once. Terminal — the part is never offered again."""
    return DeclineResponse(
        declined=ingest_svc.decline_candidates(
            caller.user_id, [(r.signal_id, r.part_index) for r in body.refs]
        )
    )


@router.post(
    "/attachment-ingest/candidates/undo-decline",
    dependencies=[Depends(require_same_origin)],
)
def undo_decline(
    body: UndoDeclineRequest,
    caller: UserContext = Depends(require_user),
) -> dict:
    """Put a declined attachment back in the list. No bytes ever moved for it."""
    return {
        "restored": ingest_svc.undo_decline(caller.user_id, body.signal_id, int(body.part_index))
    }


@router.get("/attachment-rules")
def list_attachment_rules(
    source_id: str = Query(...),
    caller: UserContext = Depends(require_user),
) -> dict:
    _owned_imap_source(caller.user_id, source_id)
    return {
        "rules": [
            AttachmentRuleModel(
                id=str(r["id"]),
                match_type=str(r["match_type"]),
                match_value=str(r["match_value"]),
                created_at=_iso(r.get("created_at")),
                stale=bool(r.get("stale")),
            ).model_dump()
            for r in rules_svc.list_rules(caller.user_id, source_id)
        ]
    }


@router.post(
    "/attachment-rules",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_same_origin)],
)
def create_attachment_rule(
    body: CreateRuleRequest,
    caller: UserContext = Depends(require_user),
) -> dict:
    _owned_imap_source(caller.user_id, body.source_id)
    # LUM-728 — gate BEFORE the write. A `high`-confidence overlap means
    # paperless already files this mailbox, so the rule would file everything
    # twice. `low` never gates (ADR D3): it is frequent enough that gating on it
    # would train dismissal, and the dismissal transfers to the `high` gate that
    # mattered.
    #
    # LUM-743 — an acknowledgement counts only for the mailbox it names. A claim
    # that does not match this request's `source_id` is treated as no claim at
    # all, so the probe runs and the user is asked again: fail closed, and
    # graceful — a client bug re-prompts rather than silently filing twice.
    acked = bool(body.acknowledge_paperless_overlap) and (
        body.acknowledged_source_id == body.source_id
    )
    if body.acknowledge_paperless_overlap and not acked:
        # Loud in the log, because this is only reachable from a client defect
        # and the symptom — a refusal the user already answered — looks like a
        # server bug from the outside.
        _log.warning(
            "create_attachment_rule: overlap acknowledgement names a different mailbox "
            "than the rule (rule source %s); treating as unacknowledged",
            body.source_id,
        )
    if not acked:
        result = overlap_svc.overlap_for_source(caller.user_id, body.source_id)
        if result.overlap is not None and result.overlap.confidence == "high":
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=overlap_svc.refusal_detail(result.overlap),
            )
    try:
        return rules_svc.create_rule(
            caller.user_id, body.source_id, body.match_type, body.match_value
        )
    except FileExistsError:
        raise HTTPException(status.HTTP_409_CONFLICT, detail={"error": "rule_exists"}) from None
    except ValueError as exc:
        # A category rule naming a slug that does not exist can never match, and
        # a rule that silently never fires reads as the feature being broken.
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": "invalid_rule", "message": str(exc)},
        ) from None


#: Its own bucket, sized like the `/email` read siblings (LUM-831's
#: sending-addresses uses the same). Fired on mount, not per keystroke.
_overlap_limiter = RequestRateLimiter(max_requests=20, window_seconds=60)

#: `creating` — copy for a rule the caller is about to make (the shipped tense).
#: `existing` — copy for rules that already exist (LUM-836's rules page).
#: Default is `creating` so the shipped single-source contract is unchanged.
OverlapContext = Literal["creating", "existing"]


def _overlap_message(ov: overlap_svc.Overlap, context: str) -> str:
    """Pick the module constant. **The server owns this copy.**

    `paperlessOverlapWarning.ts` matches on `code` and never on prose,
    specifically so wording can move without a contract break — choosing the
    string client-side would invert that. Present tense is not cosmetic: on the
    rules page the future-tense copy describes an action the reader is not
    taking, which reads as a bug in the product rather than a warning about one.
    """
    existing = context == "existing"
    if ov.confidence == "low":
        return overlap_svc.ADVISORY_EXISTING if existing else overlap_svc.ADVISORY
    if not existing:
        return overlap_svc.refusal_detail(ov)["message"]
    return (
        overlap_svc.MESSAGE_EXISTING_DELETE
        if ov.action == overlap_svc._ACTION_DELETE
        else overlap_svc.MESSAGE_EXISTING
    )


def _overlap_payload(source_id: str, result: overlap_svc.OverlapResult, context: str) -> dict:
    ov = result.overlap
    # `folder` is deliberately absent: it is internal diagnostic state and a
    # folder name can be personal. `message` is only ever a module constant.
    return {
        "source_id": source_id,
        "overlap": ov is not None,
        "confidence": ov.confidence if ov else None,
        "status": result.status,
        "message": None if ov is None else _overlap_message(ov, context),
    }


@router.get("/attachment-ingest/paperless-overlap")
def get_paperless_overlap(
    source_id: str = Query(...),
    context: OverlapContext = Query("creating"),
    caller: UserContext = Depends(require_user),
) -> dict:
    """Advisory only — the POST gate is authoritative and recomputes.

    This route exists because a `low` finding never gates, so the 422 can never
    carry it. Without this the advisory has no delivery path at all.
    """
    _owned_imap_source(caller.user_id, source_id)
    result = overlap_svc.overlap_for_source(caller.user_id, source_id)
    payload = _overlap_payload(source_id, result, context)
    # The shipped single-source shape has no `source_id`; the caller already
    # knows it. Kept out so this response is byte-identical to what LUM-728
    # shipped and no existing consumer sees a new key.
    payload.pop("source_id", None)
    return payload


@router.get("/attachment-ingest/paperless-overlap/all")
def get_paperless_overlap_all(
    response: Response,
    request: Request,
    context: OverlapContext = Query("existing"),
    caller: UserContext = Depends(require_user),
) -> dict:
    """Every **rule-bearing** mailbox the caller owns, for one paperless fetch.

    **`/all` means "all your mailboxes in one request", not "every mailbox".**
    Mailboxes with no filing rule are absent: a mailbox that files nothing cannot
    duplicate anything, so probing it is cost without an answer. That predicate is
    also what makes the common case free — a user with no filing rules gets
    `items: []` having made no HTTP call and no credential decrypt.

    Exists because `overlap_for_source` performs a full `fetch_mail_config()` per
    call while accounts and rules are per-**instance**. `AttachmentRuleList` is
    mounted once per mailbox card, so a per-source read would issue one paperless
    fetch per mailbox — the pattern LUM-744 exists to remove.
    """
    if not _overlap_limiter.check(caller.user_id):
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            detail="too many overlap requests; try again in a minute",
            headers={"Retry-After": "60"},
        )
    # Mailbox identifiers and rule counts: no intermediary cache should hold
    # these across logins.
    response.headers["Cache-Control"] = "no-store"

    # One helper, shared with the `POST /sources` connect gate: both surfaces
    # need the same set — mailboxes that actually file something — and two copies
    # of that predicate is how one of them later stops matching the other.
    # Rule counts ride along from the cached per-user rules read.
    mailboxes = rules_svc.rule_bearing_mailboxes(caller.user_id)
    if not mailboxes:
        return {"items": []}
    owned = list(mailboxes)

    results = overlap_svc.overlap_for_sources(caller.user_id, owned)
    items = []
    for sid in owned:
        result = results.get(sid)
        if result is None:
            continue
        payload = _overlap_payload(sid, result, context)
        payload["source_name"] = mailboxes[sid]["source_name"]
        payload["rule_count"] = mailboxes[sid]["rule_count"]
        items.append(payload)
    return {"items": items}


@router.delete(
    "/attachment-rules/{rule_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_same_origin)],
)
def delete_attachment_rule(
    rule_id: str,
    caller: UserContext = Depends(require_user),
) -> None:
    """Stop future filing. **Already-filed documents are untouched** —
    `filed_by_rule_id` is ON DELETE SET NULL, so they lose their attribution and
    keep their undo path. Deleting a rule is not a retraction."""
    if not rules_svc.delete_rule(caller.user_id, rule_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail={"error": "not_found"})
