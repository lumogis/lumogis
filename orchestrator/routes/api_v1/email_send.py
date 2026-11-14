# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Email send-surface support routes (LUM-652).

``POST /api/v1/email/reply-draft`` returns an on-demand, on-box LLM-suggested reply
body for a surfaced email (or ``""`` when no local model / no body / a miss). It is
owner-scoped (404 for another user's signal) and rate-limited on a **separate**
bucket from the approvals limiter so draft-gen can't starve the send flow.
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from collections import deque
from typing import Deque
from typing import Literal
from typing import NamedTuple

from actions.errors import PermanentActionError
from adapters.email_imap_source import EmailImapSource
from adapters.email_imap_source import FetchedBody
from auth import get_user
from authz import require_user
from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException
from fastapi import Request
from fastapi import status
from pydantic import BaseModel
from pydantic import Field
from pydantic import model_validator
from rate_limit import RequestRateLimiter
from services.email_body import annotate_control_chars
from services.email_message_locate import message_id_for as _message_id_for
from services.email_message_locate import organize_folder_for as _organize_folder_for

from services import email_quote
from services import email_reply_draft
from services import text_refine

_log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/email", tags=["email-send"])


# In-process token bucket: 20 draft requests / 60s / user — a SEPARATE bucket from
# the approvals limiter (an LLM call budget; sharing would let draft-spam starve the
# user's own create/approve/execute send flow).
_DRAFT_WINDOW_SEC = 60.0
_DRAFT_LIMIT = 20
_draft_calls: dict[str, Deque[float]] = defaultdict(deque)


def _draft_rate_check(request: Request) -> None:
    user_id = get_user(request).user_id
    now = time.monotonic()
    bucket = _draft_calls[user_id]
    while bucket and now - bucket[0] > _DRAFT_WINDOW_SEC:
        bucket.popleft()
    if len(bucket) >= _DRAFT_LIMIT:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="too many draft requests; try again in a minute",
            headers={"Retry-After": "60"},
        )
    bucket.append(now)


# Quote (LUM-698) gets its OWN bucket too. Unlike its siblings this is a bounded
# IMAP fetch, not an LLM call — so it must not consume the LLM budgets, and
# quote-spam must not starve the draft/refine/send flows. Same 20/60s shape as
# the siblings rather than an invented number.
_QUOTE_WINDOW_SEC = 60.0
_QUOTE_LIMIT = 20
_quote_calls: dict[str, Deque[float]] = defaultdict(deque)


def _quote_rate_check(request: Request) -> None:
    user_id = get_user(request).user_id
    now = time.monotonic()
    bucket = _quote_calls[user_id]
    while bucket and now - bucket[0] > _QUOTE_WINDOW_SEC:
        bucket.popleft()
    if len(bucket) >= _QUOTE_LIMIT:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="too many quote requests; try again in a minute",
            headers={"Retry-After": "60"},
        )
    bucket.append(now)


# Refine (LUM-684) gets its OWN bucket — a separate LLM-call budget so refine-spam
# can't starve the reply-draft flow (or vice versa).
_REFINE_WINDOW_SEC = 60.0
_REFINE_LIMIT = 20
_refine_calls: dict[str, Deque[float]] = defaultdict(deque)


def _refine_rate_check(request: Request) -> None:
    user_id = get_user(request).user_id
    now = time.monotonic()
    bucket = _refine_calls[user_id]
    while bucket and now - bucket[0] > _REFINE_WINDOW_SEC:
        bucket.popleft()
    if len(bucket) >= _REFINE_LIMIT:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="too many refine requests; try again in a minute",
            headers={"Retry-After": "60"},
        )
    bucket.append(now)


class ReplyDraftRequest(BaseModel):
    signal_id: str


class ReplyDraftResponse(BaseModel):
    draft: str


@router.post(
    "/reply-draft",
    response_model=ReplyDraftResponse,
    dependencies=[Depends(require_user), Depends(_draft_rate_check)],
)
def reply_draft(body: ReplyDraftRequest, request: Request) -> ReplyDraftResponse:
    """Suggest a reply body for the caller's own signal. 200 with ``draft==""`` on
    no-model/miss; 404 only when the signal is not the caller's own (no oracle)."""
    caller = get_user(request)
    _assert_owned_signal(body.signal_id, caller.user_id)
    draft = email_reply_draft.suggest_reply_draft(body.signal_id, user_id=caller.user_id)
    return ReplyDraftResponse(draft=draft)


# --------------------------------------------------------------------------- #
# Read view (LUM-810)
# --------------------------------------------------------------------------- #

# Its OWN bucket, for the reason /quote and /reply-all have theirs: a bounded
# IMAP fetch must not consume the LLM budgets. Built on the shared
# `RequestRateLimiter` rather than a fifth hand-rolled deque — the local
# pattern in this module carries no lock, and sync FastAPI handlers run in a
# threadpool, so concurrent requests from one user can race it.
_BODY_LIMIT = 20
_body_limiter = RequestRateLimiter(max_requests=_BODY_LIMIT, window_seconds=60.0)

# Post-extraction ceiling on what is shipped and rendered, matching
# `_MAX_QUOTE_CHARS`. Distinct from the adapter's `_MAX_BODY_BYTES`, which
# bounds the download and the parser: bytes bound cost, chars bound payload.
_MAX_BODY_CHARS = 20_000


def _body_rate_check(request: Request) -> None:
    if not _body_limiter.check(get_user(request).user_id):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="too many body requests; try again in a minute",
            headers={"Retry-After": "60"},
        )


class BodyRequest(BaseModel):
    signal_id: str


class InlineImageOut(BaseModel):
    """One inline image, carried inside the body response (LUM-713).

    There is deliberately **no part number and no fetch handle** here. Nothing
    the client holds is ever used to address a MIME part, so there is nothing
    for a client to tamper with; the bytes arrive with the text, in the one
    request the user's single action already paid for.
    """

    content_id: str
    mime_type: str  # the SNIFFED type; the declared one never reaches here
    size_bytes: int  # decoded, pre-base64
    data_uri: str


class BodyResponse(BaseModel):
    body: str = ""
    truncated: bool = False
    degraded: bool = False
    reason: Literal[
        "ok", "no_body", "mailbox_unreachable", "not_found", "too_large", "ambiguous"
    ] = "ok"
    inline_images: list[InlineImageOut] = []
    # A CAP dropped an image, so the UI can say how many are missing. A part
    # that was *refused* (an SVG announced as a PNG) never sets this: it was
    # never content the user could have seen.
    inline_images_truncated: bool = False


@router.post(
    "/body",
    response_model=BodyResponse,
    dependencies=[Depends(require_user), Depends(_body_rate_check)],
)
def body(body: BodyRequest, request: Request) -> BodyResponse:
    """The readable text of the caller's own message (LUM-810).

    **Extracted text, never rendered HTML.** No sender-controlled bytes enter a
    rendered HTML context, so the containment question a sanitiser would raise
    is avoided rather than answered.

    200 with a `reason` for every expected failure — the mailbox being down,
    the message being gone or too large, or a Message-ID several messages
    claim. 404 only when the signal is not the caller's own, with the same
    shape as a genuine miss so the gate is not an oracle.
    """
    caller = get_user(request)
    owned = _assert_owned_signal(body.signal_id, caller.user_id)

    # `EmailImapSource.__init__` loads the mailbox credential — and for an
    # xoauth2 mailbox that means a live token refresh, which a revoked grant or
    # an unreachable token endpoint fails. Constructing OUTSIDE this try would
    # 500 and break the contract stated above on the one condition most likely
    # to occur in the field.
    try:
        fetched = EmailImapSource(owned.source).fetch_body_bounded(
            uid=owned.uid,
            # Both lookups come from `email_attachment_ingest`, NOT from the
            # copies in `routes/api_v1/email_attachments.py`: those read
            # `email_organize_log` only, so they resolve a Message-ID solely for
            # mail *Lumogis itself* moved. Mail the user filed in their own
            # client, or a server-side rule moved, would report "no longer in
            # your mailbox" while sitting right there — the hole migration 069
            # was written to close.
            message_id=_message_id_for(body.signal_id, caller.user_id),
            folder_hint=_organize_folder_for(body.signal_id, caller.user_id),
            # The uid is only usable while this still matches; past a reset the
            # adapter falls back to the Message-ID search.
            uidvalidity=owned.uidvalidity,
        )
    except Exception as exc:  # noqa: BLE001 - no expected failure may be a 5xx
        _log.warning(
            "email_body_unavailable signal_id=%s type=%s",
            body.signal_id,
            type(exc).__name__,  # never the message: it can carry mailbox detail
        )
        fetched = FetchedBody("", "mailbox_unreachable")

    # Truncate BEFORE annotating. Annotation expands a 1-char control into an
    # 8-char sentinel, so slicing afterwards can cut one in half — a visible
    # `␎U+` tail — and makes the cap bound the escaping rather than the message.
    truncated = len(fetched.text) > _MAX_BODY_CHARS
    text = annotate_control_chars(fetched.text[:_MAX_BODY_CHARS])
    return BodyResponse(
        body=text,
        truncated=truncated,
        degraded=fetched.reason == "mailbox_unreachable",
        reason=fetched.reason,
        # Already identified by their own bytes and bounded by the adapter's
        # caps (LUM-713). Restated as a model rather than passed through, so
        # only the four fields above can ever cross the wire.
        inline_images=[
            InlineImageOut(
                content_id=image.content_id,
                mime_type=image.mime_type,
                size_bytes=image.size_bytes,
                data_uri=image.data_uri,
            )
            for image in fetched.inline_images
        ],
        inline_images_truncated=fetched.inline_images_truncated,
    )


class QuoteRequest(BaseModel):
    signal_id: str
    style: Literal["reply", "forward"] = "reply"


class QuoteResponse(BaseModel):
    quote: str


@router.post(
    "/quote",
    response_model=QuoteResponse,
    dependencies=[Depends(require_user), Depends(_quote_rate_check)],
)
def quote(body: QuoteRequest, request: Request) -> QuoteResponse:
    """The quoted-original block for the caller's own signal (LUM-698).

    200 with ``quote==""`` when there is no body to quote; 404 only when the
    signal is not the caller's own (no oracle) — same posture as /reply-draft.
    The quote is returned to the composer so the user can SEE and EDIT it before
    an irreversible send; it is never applied silently at send time.
    """
    caller = get_user(request)
    _assert_owned_signal(body.signal_id, caller.user_id)
    text = email_quote.build_quote(body.signal_id, user_id=caller.user_id, style=body.style)
    return QuoteResponse(quote=text)


# Reply-all (LUM-705) gets its OWN bucket, for the same reason /quote does: a
# bounded IMAP fetch must not consume the LLM budgets, and reply-all spam must not
# starve the draft/refine/send flows.
_REPLY_ALL_WINDOW_SEC = 60.0
_REPLY_ALL_LIMIT = 20
_reply_all_calls: dict[str, Deque[float]] = defaultdict(deque)


def _reply_all_rate_check(request: Request) -> None:
    user_id = get_user(request).user_id
    now = time.monotonic()
    bucket = _reply_all_calls[user_id]
    while bucket and now - bucket[0] > _REPLY_ALL_WINDOW_SEC:
        bucket.popleft()
    if len(bucket) >= _REPLY_ALL_LIMIT:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="too many reply-all requests; try again in a minute",
            headers={"Retry-After": "60"},
        )
    bucket.append(now)


class ReplyAllRequest(BaseModel):
    signal_id: str


class ReplyAllResponse(BaseModel):
    to: list[str]
    cc: list[str]
    # True when the original's headers could not be re-fetched. ALWAYS shipped
    # with an empty `cc`, so a client that ignores the flag still behaves as a
    # plain Reply rather than sending to nobody.
    degraded: bool
    dropped_self: bool
    truncated: int
    large_thread: bool


@router.post(
    "/reply-all",
    response_model=ReplyAllResponse,
    dependencies=[Depends(require_user), Depends(_reply_all_rate_check)],
)
def reply_all(body: ReplyAllRequest, request: Request) -> ReplyAllResponse:
    """Recipients for a Reply-all on the caller's own signal (LUM-705).

    **200 with ``degraded=true``, never a 4xx/5xx, for a header miss.** The signal
    is the caller's own and the request is well-formed; it is the *mailbox* that
    did not answer. A 404 here would be indistinguishable from "not your signal"
    and would show a permissions error for a transient network blip.

    The UIDVALIDITY check is not optional: a reset means the stored uid now
    identifies a **different message**, and using its headers would put a
    stranger's thread participants into this user's Cc. It lives in
    ``smtp_send.fetch_original_headers``, shared with the send path so the two
    cannot drift.
    """
    from actions.handlers.smtp_send import fetch_original_headers

    from services import email_credentials
    from services import email_reply_all

    caller = get_user(request)
    owned = _assert_owned_signal(body.signal_id, caller.user_id)
    sender = _signal_sender(body.signal_id, caller.user_id)

    headers = fetch_original_headers(owned.source, owned.uidvalidity, owned.uid)
    if headers is None:
        return ReplyAllResponse(
            to=[sender] if sender else [],
            cc=[],
            degraded=True,
            dropped_self=False,
            truncated=0,
            large_thread=False,
        )

    try:
        own_addr = email_credentials.mailbox_account_address(
            caller.user_id, instance=str(getattr(owned.source, "id", "") or "")
        )
    except Exception:  # noqa: BLE001 — an unknown own-address is not a failure
        # Degrading here would be wrong: the Cc is still useful, we just cannot
        # filter the user out of it. Better to show it — visibly, editably — than
        # to silently drop the whole feature. The duplicate is one line to delete.
        own_addr = ""

    result = email_reply_all.reply_all_recipients(
        headers, own_addr=own_addr, sender_addr=sender or ""
    )
    return ReplyAllResponse(
        to=result.to,
        cc=result.cc,
        degraded=False,
        dropped_self=result.dropped_self,
        truncated=result.truncated,
        large_thread=result.large_thread,
    )


def _signal_sender(signal_id: str, user_id: str) -> str:
    """The stored ``from_addr`` for the caller's own signal ("" when absent)."""
    import config

    ms = config.get_metadata_store()
    # SCOPE-EXEMPT: ownership is already established by `_assert_owned_signal`
    # above; this reads one column of that same signal, bound to the caller.
    row = ms.fetch_one(
        "SELECT from_addr FROM signals WHERE signal_id = %s AND user_id = %s LIMIT 1",
        (str(signal_id), user_id),
    )
    return str((row or {}).get("from_addr") or "").strip()


class OwnedSignal(NamedTuple):
    """What the ownership gate already had to resolve to answer "is this yours?".

    Returned so a caller needing the mailbox (LUM-695's attachment fetch) does
    not re-derive it — and, more importantly, does not reach into
    ``actions.handlers.imap_writeback``'s privates to do so. This module is the
    single routes→handlers coupling point for signal ownership; keep it that way.
    """

    source: object  # SourceConfig
    uidvalidity: int
    uid: int


def _assert_owned_signal(signal_id: str, user_id: str) -> OwnedSignal:
    """404 unless ``signal_id`` resolves to a mailbox owned by ``user_id``.

    404 rather than 403 throughout the email family: a 403 would confirm the
    signal exists, turning the gate into an oracle over other users' mail.
    """
    from actions.handlers.imap_writeback import _load_owned_source
    from actions.handlers.imap_writeback import _parse_imap_url
    from actions.handlers.imap_writeback import _signal_url

    url = _signal_url(signal_id)
    recovered = _parse_imap_url(url) if url else None
    if recovered is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail={"error": "not_found"})
    source_id, uidvalidity, uid = recovered
    try:
        source = _load_owned_source(source_id, user_id)
    except PermanentActionError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail={"error": "not_found"}) from None
    return OwnedSignal(source=source, uidvalidity=int(uidvalidity), uid=int(uid))


# --------------------------------------------------------------------------- #
# Refine (LUM-684): on-box tone/length transform of the caller's own composer body
# --------------------------------------------------------------------------- #
class RefineRequest(BaseModel):
    # `text` max_length is aligned with the refine input-token budget (~2000 tokens
    # ≈ 8000 chars) so a valid body is never silently truncated.
    text: str = Field(min_length=1, max_length=8000)
    preset: str | None = None
    instruction: str | None = Field(default=None, max_length=400)

    @model_validator(mode="after")
    def _exactly_one(self) -> "RefineRequest":
        has_preset = self.preset is not None
        has_instruction = self.instruction is not None and self.instruction.strip() != ""
        if has_preset == has_instruction:
            raise ValueError("provide exactly one of preset or instruction")
        if has_preset and self.preset not in text_refine.PRESET_INSTRUCTIONS:
            raise ValueError("unknown preset")
        return self


class RefineResponse(BaseModel):
    text: str
    available: bool


@router.post(
    "/refine",
    response_model=RefineResponse,
    dependencies=[Depends(require_user), Depends(_refine_rate_check)],
)
def refine(body: RefineRequest, request: Request) -> RefineResponse:
    """Rewrite the caller's own composer body per a preset or free-form instruction
    using the on-box model. 200 with ``available==false`` when no local model is
    configured (never a cloud call); the transformed text otherwise (``""`` on an
    LLM miss, which the web treats as "try again")."""
    user_id = get_user(request).user_id
    model = text_refine.resolve_refine_model(user_id)
    if not model:
        return RefineResponse(text="", available=False)
    instruction = (
        text_refine.PRESET_INSTRUCTIONS[body.preset]
        if body.preset is not None
        else (body.instruction or "")
    )
    result = text_refine.refine_text(
        body.text, instruction=instruction, user_id=user_id, model=model
    )
    return RefineResponse(text=result, available=True)
