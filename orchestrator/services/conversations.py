# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Conversation history CRUD — list, detail, continue seed, delete (LUM-162)."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from datetime import timezone
from typing import Any

from models.api_v1 import ChatMessageDTO
from models.api_v1 import ConversationDetail
from models.api_v1 import ConversationMessage
from models.api_v1 import ConversationSummary
from services.context_budget import truncate_text
from services.memory_purge import conversation_purge_target_exists
from services.memory_purge import purge_session_memory

import config

_log = logging.getLogger(__name__)

_SUMMARY_TITLE_MAX = 80
_CONTINUE_SUMMARY_MAX_CHARS = 4000


class ConversationNotFoundError(Exception):
    """Raised when the caller has no personal conversation row for the id."""


def _title_from_summary(summary: str) -> str:
    for line in (summary or "").splitlines():
        stripped = line.strip()
        if stripped:
            return stripped[:_SUMMARY_TITLE_MAX]
    return "Chat"


def _row_to_summary(row: dict[str, Any], *, message_count: int | None = None) -> ConversationSummary:
    wc_title = row.get("wc_title")
    title = (wc_title or "").strip() if wc_title else _title_from_summary(row.get("summary") or "")
    if not title:
        title = "Chat"
    return ConversationSummary(
        conversation_id=str(row["session_id"]),
        title=title,
        summary=row.get("summary") or "",
        ended_at=row["updated_at"],
        scope=row.get("scope") or "personal",
        message_count=message_count if message_count is not None else row.get("message_count"),
    )


def list_conversations(
    user_id: str,
    *,
    limit: int = 50,
    scope_filter: str = "personal",
) -> list[ConversationSummary]:
    """List ended conversations for the personal history sidebar."""
    ms = config.get_metadata_store()
    try:
        rows = ms.fetch_all(
            """
            SELECT s.session_id, s.summary, s.scope, s.updated_at,
                   wc.title AS wc_title, wc.message_count
            FROM sessions s
            LEFT JOIN web_conversations wc
              ON wc.conversation_id = s.session_id AND wc.user_id = s.user_id
            WHERE s.user_id = %s AND s.scope = %s
            ORDER BY s.updated_at DESC
            LIMIT %s
            """,
            (user_id, scope_filter, limit),
        )
    except Exception as exc:
        _log.warning("list_conversations: DB query failed — %s", exc)
        return []

    return [_row_to_summary(r) for r in rows]


def get_conversation(user_id: str, conversation_id: str) -> ConversationDetail:
    """Return conversation detail; includes verbatim messages when web_messages exist."""
    ms = config.get_metadata_store()
    row = ms.fetch_one(
        """
        SELECT s.session_id, s.summary, s.topics, s.entities, s.scope, s.updated_at,
               wc.title AS wc_title, wc.message_count
        FROM sessions s
        LEFT JOIN web_conversations wc
          ON wc.conversation_id = s.session_id AND wc.user_id = s.user_id
        WHERE s.session_id = %s::uuid AND s.user_id = %s
        """,
        (conversation_id, user_id),
    )
    if not row:
        raise ConversationNotFoundError(conversation_id)

    messages: list[ConversationMessage] = []
    try:
        msg_rows = ms.fetch_all(
            """
            SELECT message_id, role, content, model, created_at
            FROM web_messages
            WHERE conversation_id = %s::uuid AND user_id = %s
            ORDER BY created_at ASC
            """,
            (conversation_id, user_id),
        )
        for m in msg_rows:
            messages.append(
                ConversationMessage(
                    message_id=str(m["message_id"]),
                    role=m["role"],
                    content=m["content"],
                    created_at=m["created_at"],
                    model=m.get("model"),
                )
            )
    except Exception:
        pass

    summary = _row_to_summary(row)
    return ConversationDetail(
        conversation_id=summary.conversation_id,
        title=summary.title,
        summary=summary.summary,
        topics=list(row.get("topics") or []),
        entities=list(row.get("entities") or []),
        ended_at=summary.ended_at,
        scope=summary.scope,
        messages=messages,
    )


def delete_conversation(user_id: str, conversation_id: str):
    """Hard-delete conversation; raises ConversationNotFoundError when not deletable."""
    if not conversation_purge_target_exists(user_id=user_id, session_id=conversation_id):
        raise ConversationNotFoundError(conversation_id)
    return purge_session_memory(user_id=user_id, session_id=conversation_id)


def build_continue_seed(detail: ConversationDetail) -> list[ChatMessageDTO]:
    """Slice 1: summary context message; slice 2: verbatim transcript when present."""
    if detail.messages:
        return [
            ChatMessageDTO(role=m.role, content=m.content)
            for m in detail.messages
            if m.role in ("user", "assistant", "system")
        ]

    topics_joined = ", ".join(detail.topics) if detail.topics else "(none)"
    raw_summary = (detail.summary or "")[:_CONTINUE_SUMMARY_MAX_CHARS]
    summary_text = truncate_text(raw_summary, max_tokens=1024)
    body = (
        "[Prior conversation context]\n"
        f"Summary: {summary_text}\n"
        f"Topics: {topics_joined}"
    )
    return [ChatMessageDTO(role="user", content=body)]


def upsert_web_conversation(
    *,
    user_id: str,
    conversation_id: str,
    title: str = "",
    model: str = "",
) -> None:
    """Create or touch the web transcript header row (slice 2 sync)."""
    ms = config.get_metadata_store()
    ms.execute(
        """
        INSERT INTO web_conversations (conversation_id, user_id, title, model, scope)
        VALUES (%s::uuid, %s, %s, %s, 'personal')
        ON CONFLICT (conversation_id) DO UPDATE
          SET updated_at = NOW(),
              title = CASE WHEN EXCLUDED.title <> '' THEN EXCLUDED.title ELSE web_conversations.title END,
              model = CASE WHEN EXCLUDED.model <> '' THEN EXCLUDED.model ELSE web_conversations.model END
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
) -> ConversationMessage:
    """Append one message idempotently (client-supplied message_id)."""
    ms = config.get_metadata_store()
    owned = ms.fetch_one(
        "SELECT conversation_id FROM web_conversations "
        "WHERE conversation_id = %s::uuid AND user_id = %s",
        (conversation_id, user_id),
    )
    if not owned:
        raise ConversationNotFoundError(conversation_id)

    ms.execute(
        """
        INSERT INTO web_messages (message_id, conversation_id, user_id, role, content, model)
        VALUES (%s::uuid, %s::uuid, %s, %s, %s, %s)
        ON CONFLICT (message_id) DO NOTHING
        """,
        (message_id, conversation_id, user_id, role, content, model),
    )
    row = ms.fetch_one(
        "SELECT message_id, role, content, model, created_at FROM web_messages "
        "WHERE message_id = %s::uuid",
        (message_id,),
    )
    if not row:
        raise ConversationNotFoundError(message_id)
    return ConversationMessage(
        message_id=str(row["message_id"]),
        role=row["role"],
        content=row["content"],
        created_at=row["created_at"],
        model=row.get("model"),
    )


def update_web_conversation(
    *,
    user_id: str,
    conversation_id: str,
    title: str | None = None,
    model: str | None = None,
) -> ConversationSummary:
    """Patch title/model on an owned web_conversations row."""
    ms = config.get_metadata_store()
    row = ms.fetch_one(
        "SELECT conversation_id FROM web_conversations "
        "WHERE conversation_id = %s::uuid AND user_id = %s",
        (conversation_id, user_id),
    )
    if not row:
        raise ConversationNotFoundError(conversation_id)
    if title is not None:
        ms.execute(
            "UPDATE web_conversations SET title = %s, updated_at = NOW() "
            "WHERE conversation_id = %s::uuid AND user_id = %s",
            (title, conversation_id, user_id),
        )
    if model is not None:
        ms.execute(
            "UPDATE web_conversations SET model = %s, updated_at = NOW() "
            "WHERE conversation_id = %s::uuid AND user_id = %s",
            (model, conversation_id, user_id),
        )
    detail = get_conversation(user_id, conversation_id)
    return ConversationSummary(
        conversation_id=detail.conversation_id,
        title=detail.title,
        summary=detail.summary,
        ended_at=detail.ended_at,
        scope=detail.scope,
        message_count=len(detail.messages) or None,
    )


def create_web_conversation(*, user_id: str, title: str = "", model: str = "") -> ConversationSummary:
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
