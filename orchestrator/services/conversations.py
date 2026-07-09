# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Conversation history CRUD — list, detail, continue seed, delete (LUM-162)."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime
from datetime import timezone
from typing import Any

from auth import UserContext
from models.api_v1 import ChatMessageDTO
from models.api_v1 import ConversationDetail
from models.api_v1 import ConversationMessage
from models.api_v1 import ConversationSummary
from services.context_budget import truncate_text
from services.memory_purge import conversation_purge_target_exists
from services.memory_purge import is_conversation_purged
from services.memory_purge import purge_session_memory
from visibility import visible_filter

import config

_log = logging.getLogger(__name__)

_SUMMARY_TITLE_MAX = 80
_CONTINUE_SUMMARY_MAX_CHARS = 4000


class ConversationNotFoundError(Exception):
    """Raised when the caller has no personal conversation row for the id."""


class ActionProposalNotFoundError(Exception):
    """Raised when action_proposal_id does not exist."""

    def __init__(self, proposal_id: int) -> None:
        self.proposal_id = proposal_id
        super().__init__(f"action proposal {proposal_id} not found")


def _title_from_summary(summary: str) -> str:
    for line in (summary or "").splitlines():
        stripped = line.strip()
        if stripped:
            return stripped[:_SUMMARY_TITLE_MAX]
    return "Chat"


def _parse_source_refs(raw: Any) -> list[dict[str, Any]] | None:
    if raw is None:
        return None
    if isinstance(raw, str):
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, list) else None
    if isinstance(raw, list):
        return raw
    return None


def _message_from_row(row: dict[str, Any]) -> ConversationMessage:
    return ConversationMessage(
        message_id=str(row["message_id"]),
        role=row["role"],
        content=row["content"],
        created_at=row["created_at"],
        model=row.get("model"),
        source_refs=_parse_source_refs(row.get("source_refs")),
        action_proposal_id=row.get("action_proposal_id"),
    )


def _derive_share_fields(
    row: dict[str, Any],
    *,
    caller_user_id: str,
    shared_source_ids: set[str],
) -> tuple[str, bool]:
    """Return ``(share_status, is_owner)`` for a conversation row (LUM-582).

    A shared projection carries the publisher's ``user_id`` and a
    ``published_from`` source id; the owner's own projection is collapsed out of
    their list, so any shared-scope row that survives belongs to another member
    (``is_owner=False``). A personal source row whose id has a shared projection
    reports ``share_status='shared'`` so the owner sees their row as shared.
    """
    scope = row.get("scope") or "personal"
    # Any shared/system row the caller can see is another member's projection
    # (the caller's own is collapsed out) — mirror documents' derivation and do
    # not require ``published_from`` so a shared row is never mislabelled personal.
    if scope in ("shared", "system"):
        return "shared", row.get("user_id") == caller_user_id
    # Personal source row: shared iff the caller has a shared projection of it.
    if str(row.get("session_id")) in shared_source_ids:
        return "shared", True
    return "personal", True


def _fetch_shared_session_ids(user_id: str) -> set[str]:
    """Source session ids the caller currently has a shared projection for."""
    ms = config.get_metadata_store()
    try:
        rows = ms.fetch_all(
            # SCOPE-EXEMPT: caller-owned shared session projection sources.
            "SELECT DISTINCT published_from FROM sessions "
            "WHERE user_id = %s AND scope = 'shared' AND published_from IS NOT NULL",
            (user_id,),
        )
    except Exception as exc:  # noqa: BLE001 — best-effort enrichment
        _log.warning("list_conversations: shared-source query failed — %s", exc)
        return set()
    return {str(r["published_from"]) for r in rows if r.get("published_from") is not None}


def _row_to_summary(
    row: dict[str, Any],
    *,
    message_count: int | None = None,
    caller_user_id: str | None = None,
    shared_source_ids: set[str] | None = None,
) -> ConversationSummary:
    wc_title = row.get("wc_title")
    title = (wc_title or "").strip() if wc_title else _title_from_summary(row.get("summary") or "")
    if not title:
        title = "Chat"
    share_status, is_owner = "personal", True
    if caller_user_id is not None:
        share_status, is_owner = _derive_share_fields(
            row, caller_user_id=caller_user_id, shared_source_ids=shared_source_ids or set()
        )
    return ConversationSummary(
        conversation_id=str(row["session_id"]),
        title=title,
        summary=row.get("summary") or "",
        ended_at=row["updated_at"],
        scope=row.get("scope") or "personal",
        message_count=message_count if message_count is not None else row.get("message_count"),
        share_status=share_status,
        is_owner=is_owner,
        can_share=True,  # every row here is a backed (summarized) sessions row
        shared_summary=None,  # attribution text is a detail-view affordance
    )


def list_conversations(
    user: UserContext,
    *,
    limit: int = 50,
    scope_filter: str | None = None,
) -> list[ConversationSummary]:
    """List conversations visible to the caller (own personal + household shared).

    Uses the ``visible_filter()`` household union so a member sees conversations
    shared with the household, and collapses the caller's own projection
    duplicate (they see their personal row, marked ``share_status='shared'``).
    """
    ms = config.get_metadata_store()
    caller = user.user_id
    vis_clause, vis_params = visible_filter(user, scope_filter)
    shared_source_ids = _fetch_shared_session_ids(caller)
    try:
        rows = ms.fetch_all(
            # ``visible_filter`` emits bare ``scope``/``user_id`` — scope the
            # sessions scan in a derived table so those columns are unambiguous
            # against the ``web_conversations`` join. The collapse predicate hides
            # the caller's OWN shared projection (they keep the personal source).
            f"""
            SELECT s.session_id, s.summary, s.scope, s.updated_at, s.user_id, s.published_from,
                   wc.title AS wc_title, wc.message_count
            FROM (
                SELECT session_id, summary, scope, updated_at, user_id, published_from
                FROM sessions
                WHERE {vis_clause}
                  AND NOT (published_from IS NOT NULL AND user_id = %s)
            ) s
            LEFT JOIN web_conversations wc
              ON wc.conversation_id = s.session_id AND wc.user_id = s.user_id
            ORDER BY s.updated_at DESC
            LIMIT %s
            """,
            (*vis_params, caller, limit),
        )
    except Exception as exc:
        _log.warning("list_conversations: DB query failed — %s", exc)
        return []

    return [
        _row_to_summary(r, caller_user_id=caller, shared_source_ids=shared_source_ids) for r in rows
    ]


def get_conversation(user: UserContext, conversation_id: str) -> ConversationDetail:
    """Return conversation detail; includes verbatim messages when web_messages exist.

    Uses the ``visible_filter()`` household union so a member can open a
    conversation shared with the household (LUM-582 Rung 1). ``can_share`` is
    ``False`` for a web-only conversation with no backing (summarized) sessions
    row. ``shared_summary`` is the household-facing (editable) summary.
    """
    ms = config.get_metadata_store()
    caller = user.user_id
    vis_clause, vis_params = visible_filter(user)
    row = ms.fetch_one(
        # The projected (shared) row's own summary IS the household-facing text;
        # for the owner's personal source row it lives on the projection, joined
        # here by ``published_from``. Sessions scan wrapped in a derived table so
        # the bare ``scope``/``user_id`` in ``vis_clause`` stay unambiguous.
        f"""
        SELECT s.session_id, s.summary, s.topics, s.entities, s.scope, s.updated_at,
               s.user_id, s.published_from, wc.title AS wc_title, wc.message_count,
               (SELECT p.summary FROM sessions p
                 WHERE p.published_from = s.session_id AND p.scope = 'shared'
                 LIMIT 1) AS proj_summary
        FROM (
            SELECT session_id, summary, topics, entities, scope, updated_at,
                   user_id, published_from
            FROM sessions
            WHERE session_id = %s::uuid AND ({vis_clause})
        ) s
        LEFT JOIN web_conversations wc
          ON wc.conversation_id = s.session_id AND wc.user_id = s.user_id
        """,
        (conversation_id, *vis_params),
    )
    can_share = True
    if not row:
        # Web-only conversation (no backing sessions row) — own-scope only, and
        # not shareable until it has been summarized into a sessions row.
        wc = ms.fetch_one(
            """
            SELECT conversation_id, title, model, message_count, updated_at, scope
            FROM web_conversations
            WHERE conversation_id = %s::uuid AND user_id = %s
            """,
            (conversation_id, caller),
        )
        if not wc:
            raise ConversationNotFoundError(conversation_id)
        can_share = False
        row = {
            "session_id": wc["conversation_id"],
            "summary": "",
            "topics": [],
            "entities": [],
            "scope": wc.get("scope") or "personal",
            "updated_at": wc["updated_at"],
            "user_id": caller,
            "published_from": None,
            "wc_title": wc.get("title"),
            "message_count": wc.get("message_count"),
            "proj_summary": None,
        }

    messages: list[ConversationMessage] = []
    try:
        from psycopg2 import errors as pg_errors

        # Rung 1 shares the summary only (snapshot), not the transcript: a
        # non-owner opening a shared projection has no web_messages keyed to the
        # projection id, so this correctly returns []. The owner viewing their own
        # source row gets their own messages.
        msg_rows = ms.fetch_all(
            """
            SELECT message_id, role, content, model, created_at,
                   source_refs, action_proposal_id
            FROM web_messages
            WHERE conversation_id = %s::uuid AND user_id = %s
            ORDER BY created_at ASC
            """,
            (conversation_id, caller),
        )
        for m in msg_rows:
            messages.append(_message_from_row(m))
    except pg_errors.UndefinedTable:
        pass

    # The detail query already resolved whether a shared projection exists
    # (``proj_summary``) for the owner's source row, so derive share_status from
    # it directly rather than a second ``_fetch_shared_session_ids`` round-trip.
    has_projection = row.get("proj_summary") is not None
    shared_source_ids = {str(row["session_id"])} if has_projection else set()
    summary = _row_to_summary(row, caller_user_id=caller, shared_source_ids=shared_source_ids)
    # A non-owner can never share; ``can_share`` is meaningful only for the owner.
    can_share = can_share and summary.is_owner
    # Resolve the household-facing summary: the projected row's own text for a
    # non-owner view; the projection joined via ``published_from`` for the owner.
    shared_summary = None
    if summary.share_status == "shared":
        shared_summary = (
            row.get("summary") if row.get("published_from") else row.get("proj_summary")
        )
    return ConversationDetail(
        conversation_id=summary.conversation_id,
        title=summary.title,
        summary=summary.summary,
        topics=list(row.get("topics") or []),
        entities=list(row.get("entities") or []),
        ended_at=summary.ended_at,
        scope=summary.scope,
        messages=messages,
        share_status=summary.share_status,
        is_owner=summary.is_owner,
        can_share=can_share,
        shared_summary=shared_summary,
    )


def delete_conversation(user_id: str, conversation_id: str):
    """Hard-delete conversation; raises ConversationNotFoundError when not deletable."""
    if not conversation_purge_target_exists(user_id=user_id, session_id=conversation_id):
        raise ConversationNotFoundError(conversation_id)
    return purge_session_memory(user_id=user_id, session_id=conversation_id)


def build_continue_seed(detail: ConversationDetail) -> list[ChatMessageDTO]:
    """Slice 1: summary context message; slice 2: verbatim transcript when present."""
    verbatim = [
        ChatMessageDTO(role=m.role, content=m.content)
        for m in detail.messages
        if m.role in ("user", "assistant", "system")
    ]
    # Incomplete slice-2 sync (e.g. assistant-only rows) must not shadow the
    # slice-1 summary — that would drop the user's side of the conversation.
    if verbatim and any(m.role == "user" for m in verbatim):
        return verbatim

    topics_joined = ", ".join(detail.topics) if detail.topics else "(none)"
    raw_summary = (detail.summary or "")[:_CONTINUE_SUMMARY_MAX_CHARS]
    summary_text = truncate_text(raw_summary, max_tokens=1024)
    body = f"[Prior conversation context]\nSummary: {summary_text}\nTopics: {topics_joined}"
    return [ChatMessageDTO(role="user", content=body)]


def _web_conversation_summary(user_id: str, conversation_id: str) -> ConversationSummary:
    """Build API summary from ``web_conversations`` (sessions row optional)."""
    ms = config.get_metadata_store()
    wc = ms.fetch_one(
        """
        SELECT conversation_id, title, model, message_count, updated_at, scope
        FROM web_conversations
        WHERE conversation_id = %s::uuid AND user_id = %s
        """,
        (conversation_id, user_id),
    )
    if not wc:
        raise ConversationNotFoundError(conversation_id)
    sess = ms.fetch_one(
        "SELECT summary FROM sessions WHERE session_id = %s AND user_id = %s",
        (conversation_id, user_id),
    )
    summary_text = (sess.get("summary") if sess else "") or ""
    title = (wc.get("title") or "").strip() or _title_from_summary(summary_text) or "Chat"
    return ConversationSummary(
        conversation_id=str(wc["conversation_id"]),
        title=title,
        summary=summary_text,
        ended_at=wc["updated_at"],
        scope=wc.get("scope") or "personal",
        message_count=wc.get("message_count"),
    )


def upsert_web_conversation(
    *,
    user_id: str,
    conversation_id: str,
    title: str = "",
    model: str = "",
) -> None:
    """Create or touch the web transcript header row (slice 2 sync)."""
    if is_conversation_purged(user_id=user_id, session_id=conversation_id):
        raise ConversationNotFoundError(conversation_id)
    ms = config.get_metadata_store()
    owner = ms.fetch_one(
        "SELECT user_id FROM web_conversations WHERE conversation_id = %s::uuid",
        (conversation_id,),
    )
    if owner and owner["user_id"] != user_id:
        raise ConversationNotFoundError(conversation_id)
    ms.execute(
        """
        INSERT INTO web_conversations (conversation_id, user_id, title, model, scope)
        VALUES (%s::uuid, %s, %s, %s, 'personal')
        ON CONFLICT (conversation_id) DO UPDATE
          SET updated_at = NOW(),
              title = CASE WHEN EXCLUDED.title <> ''
                  THEN EXCLUDED.title ELSE web_conversations.title END,
              model = CASE WHEN EXCLUDED.model <> ''
                  THEN EXCLUDED.model ELSE web_conversations.model END
          WHERE web_conversations.user_id = EXCLUDED.user_id
        """,
        (conversation_id, user_id, title, model),
    )


def append_web_message(
    *,
    user_id: str,
    conversation_id: str,
    message_id: str,
    role: str,
    content: str,
    model: str | None = None,
    source_refs: list[dict[str, Any]] | None = None,
    action_proposal_id: int | None = None,
) -> ConversationMessage:
    """Append one message idempotently (client-supplied message_id)."""
    if is_conversation_purged(user_id=user_id, session_id=conversation_id):
        raise ConversationNotFoundError(conversation_id)
    ms = config.get_metadata_store()
    owned = ms.fetch_one(
        "SELECT conversation_id FROM web_conversations "
        "WHERE conversation_id = %s::uuid AND user_id = %s",
        (conversation_id, user_id),
    )
    if not owned:
        raise ConversationNotFoundError(conversation_id)

    if action_proposal_id is not None:
        proposal = ms.fetch_one(
            "SELECT user_id FROM action_proposals WHERE id = %s",
            (action_proposal_id,),
        )
        if not proposal:
            raise ActionProposalNotFoundError(action_proposal_id)
        if proposal["user_id"] != user_id:
            raise ConversationNotFoundError(conversation_id)

    source_refs_param = json.dumps(source_refs) if source_refs is not None else None

    ms.execute(
        """
        INSERT INTO web_messages (
            message_id, conversation_id, user_id, role, content, model,
            source_refs, action_proposal_id
        )
        VALUES (%s::uuid, %s::uuid, %s, %s, %s, %s, %s::jsonb, %s)
        ON CONFLICT (message_id) DO NOTHING
        """,
        (
            message_id,
            conversation_id,
            user_id,
            role,
            content,
            model,
            source_refs_param,
            action_proposal_id,
        ),
    )
    row = ms.fetch_one(
        """
        SELECT message_id, role, content, model, created_at,
               source_refs, action_proposal_id
        FROM web_messages
        WHERE message_id = %s::uuid AND conversation_id = %s::uuid AND user_id = %s
        """,
        (message_id, conversation_id, user_id),
    )
    if not row:
        raise ConversationNotFoundError(conversation_id)
    return _message_from_row(row)


def update_web_conversation(
    *,
    user_id: str,
    conversation_id: str,
    title: str | None = None,
    model: str | None = None,
) -> ConversationSummary:
    """Upsert slice-2 header metadata (client-minted ``conversation_id``)."""
    upsert_web_conversation(
        user_id=user_id,
        conversation_id=conversation_id,
        title=title if title is not None else "",
        model=model if model is not None else "",
    )
    return _web_conversation_summary(user_id, conversation_id)


def create_web_conversation(
    *, user_id: str, title: str = "", model: str = ""
) -> ConversationSummary:
    """Mint a new empty server-backed conversation thread."""
    conversation_id = str(uuid.uuid4())
    upsert_web_conversation(
        user_id=user_id,
        conversation_id=conversation_id,
        title=title,
        model=model,
    )
    return ConversationSummary(
        conversation_id=conversation_id,
        title=title or "Chat",
        summary="",
        ended_at=datetime.now(timezone.utc),
        scope="personal",
        message_count=0,
    )
