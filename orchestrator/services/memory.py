# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Session memory: summarize conversations and retrieve past context.

After a session ends, the conversation is summarized using the small
local model (llama), embedded via Nomic, and stored in Qdrant's
`conversations` collection. On new queries, past session summaries
are retrieved and injected as context.
"""

import json
import logging
import uuid
from typing import Optional

import hooks
from auth import UserContext
from events import Event
from models.memory import ContextHit
from models.memory import SessionSummary
from services.context_budget import truncate_messages
from services.context_budget import truncate_text
from services.point_ids import session_conversation_point_id
from visibility import visible_filter, visible_qdrant_filter

import config

_log = logging.getLogger(__name__)

_SUMMARIZE_PROMPT = (
    "Summarize this conversation concisely. "
    "Extract: 1) a brief summary (2-3 sentences), "
    "2) the main topics discussed (as a list), "
    "3) any people, organizations, or concepts mentioned (as a list).\n\n"
    "Respond in this exact JSON format:\n"
    '{"summary": "...", "topics": ["..."], "entities": ["..."]}\n\n'
    "Conversation:\n"
)


def summarize_session(
    messages: list[dict],
    session_id: str | None = None,
    *,
    user_id: str | None = None,
) -> SessionSummary:
    """Call the small local model to summarize a conversation.

    Plan llm_provider_keys_per_user_migration Pass 2.10: ``user_id`` is
    threaded into ``get_llm_provider`` so a future switch to a cloud model
    here resolves the key per-user. ``llama`` (the current default) has no
    ``api_key_env`` so user_id is a no-op semantically.
    """
    from services.context_budget import get_budget
    from services.connector_credentials import ConnectorNotConfigured
    from services.connector_credentials import CredentialUnavailable

    budget = get_budget("llama")
    trimmed = truncate_messages(messages, max_tokens=budget - 500)

    conversation_text = "\n".join(
        f"{m.get('role', 'user')}: {m.get('content', '')}" for m in trimmed
    )
    conversation_text = truncate_text(conversation_text, max_tokens=budget - 300)

    sid = session_id or str(uuid.uuid4())
    try:
        provider = config.get_llm_provider("llama", user_id=user_id)
        response = provider.chat(
            messages=[{"role": "user", "content": _SUMMARIZE_PROMPT + conversation_text}],
            system="You are a precise summarizer. Respond only with valid JSON.",
            max_tokens=512,
        )
    except ConnectorNotConfigured as exc:
        _log.warning(
            "summarize_session: missing per-user credential (user=%s): %s",
            user_id, exc,
        )
        return SessionSummary(session_id=sid, summary="")
    except CredentialUnavailable as exc:
        _log.warning(
            "summarize_session: stored credential unusable (user=%s): %s",
            user_id, exc,
        )
        return SessionSummary(session_id=sid, summary="")

    try:
        data = json.loads(response.text)
        return SessionSummary(
            session_id=sid,
            summary=data.get("summary", response.text),
            topics=data.get("topics", []),
            entities=data.get("entities", []),
        )
    except (json.JSONDecodeError, KeyError):
        _log.warning("LLM returned non-JSON summary, using raw text")
        return SessionSummary(session_id=sid, summary=response.text)


def store_session(
    summary: SessionSummary,
    user_id: str = "default",
    entity_ids: list[str] | None = None,
    *,
    scope: str = "personal",
) -> None:
    """Store session summary in Qdrant (semantic) and Postgres (canonical).

    entity_ids: resolved entity UUIDs from store_entities().  Persisted in
    Postgres so reconciliation can replay DISCUSSED_IN edges via UUID lookup
    rather than falling back to name-string resolution.

    scope: defaults to ``'personal'``. The publish path (services/projection.py)
    creates a separate ``scope='shared'`` projection row keyed by
    ``published_from`` — this writer never mutates the personal source row.
    """
    resolved_entity_ids = entity_ids or []

    # ---- Qdrant (semantic projection) ----
    embedder = config.get_embedder()
    vs = config.get_vector_store()

    embed_text = f"{summary.summary} Topics: {', '.join(summary.topics)}"
    vector = embedder.embed(embed_text)

    point_id = session_conversation_point_id(user_id, summary.session_id)
    vs.upsert(
        collection="conversations",
        id=point_id,
        vector=vector,
        payload={
            "session_id": summary.session_id,
            "summary": summary.summary,
            "topics": summary.topics,
            "entities": summary.entities,
            "user_id": user_id,
            "scope": scope,
        },
    )

    # ---- Postgres (canonical record + entity_ids for reconciliation) ----
    ms = config.get_metadata_store()
    try:
        ms.execute(
            """
            INSERT INTO sessions (session_id, summary, topics, entities, entity_ids, user_id, scope)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (session_id) DO UPDATE
              SET summary   = EXCLUDED.summary,
                  topics    = EXCLUDED.topics,
                  entities  = EXCLUDED.entities,
                  entity_ids = EXCLUDED.entity_ids,
                  updated_at = NOW()
            """,
            (
                summary.session_id,
                summary.summary,
                summary.topics,
                summary.entities,
                resolved_entity_ids,
                user_id,
                scope,
            ),
        )
    except Exception:
        _log.warning(
            "store_session: Postgres upsert failed for session_id=%s (Qdrant write succeeded)",
            summary.session_id,
        )

    _log.info(
        "Stored session %s: %d topics, %d entities, %d entity_ids",
        summary.session_id,
        len(summary.topics),
        len(summary.entities),
        len(resolved_entity_ids),
    )
    # SESSION_ENDED hook is fired by the caller (routes/data.py) after entity
    # extraction completes, so entity_ids are available in the payload.


def recent_sessions(
    limit: int = 10,
    user_id: str = "default",
    *,
    scope_filter: Optional[str] = None,
) -> list[SessionSummary]:
    """Return the N most recent session summaries visible to ``user_id``.

    Thin Postgres helper backing the MCP `memory.get_recent` tool. No
    embedding work, no LLM work — just an ORDER BY recency read against
    the canonical `sessions` table.

    Visibility (per plan §6, scope rule):
      ``(scope='personal' AND user_id=$me) OR scope IN ('shared','system')``
    via :func:`visibility.visible_filter`. ``scope_filter`` narrows the
    union to a single arm when set; ``None`` returns the household union.
    Result rows include the ``scope`` column on the projected
    :class:`SessionSummary` so callers can render badges.

    Mirrors the error-handling pattern of routes/data.py::list_entities:
    on any DB failure the helper logs a WARNING and returns an empty
    list, never raises. Empty list is a safe answer for callers that
    cannot recover from a hard failure (the MCP tool, the dashboard).
    """
    ms = config.get_metadata_store()
    user = UserContext(user_id=user_id)
    where_clause, where_params = visible_filter(user, scope_filter)
    try:
        rows = ms.fetch_all(
            "SELECT session_id, summary, topics, entities, entity_ids, scope "
            "FROM sessions "
            f"WHERE {where_clause} "
            "ORDER BY updated_at DESC "
            "LIMIT %s",
            (*where_params, limit),
        )
    except Exception as exc:
        _log.warning("recent_sessions: DB query failed — %s", exc)
        return []

    return [
        SessionSummary(
            session_id=r["session_id"],
            summary=r["summary"] or "",
            topics=r.get("topics") or [],
            entities=r.get("entities") or [],
            entity_ids=r.get("entity_ids") or [],
            scope=r.get("scope", "personal"),
        )
        for r in rows
    ]


def retrieve_context(
    query: str,
    limit: int = 3,
    user_id: str = "default",
    *,
    scope_filter: Optional[str] = None,
) -> list[ContextHit]:
    """Search past session summaries visible to ``user_id``.

    Visibility resolved via :func:`visibility.visible_qdrant_filter` —
    default returns the household union (personal-mine + shared + system).
    Set ``scope_filter`` to narrow to a single arm.
    """
    embedder = config.get_embedder()
    vs = config.get_vector_store()

    query_vec = embedder.embed(query)
    user = UserContext(user_id=user_id)
    raw = vs.search(
        collection="conversations",
        vector=query_vec,
        limit=limit,
        threshold=0.40,
        filter=visible_qdrant_filter(user, scope_filter),
    )

    return [
        ContextHit(
            session_id=r["payload"].get("session_id", ""),
            summary=r["payload"].get("summary", ""),
            score=r["score"],
            scope=r["payload"].get("scope", "personal"),
        )
        for r in raw
    ]
