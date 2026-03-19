# SPDX-License-Identifier: AGPL-3.0-or-later
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

import hooks
from events import Event
from models.memory import ContextHit
from models.memory import SessionSummary
from services.context_budget import truncate_messages
from services.context_budget import truncate_text

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
) -> SessionSummary:
    """Call the small local model to summarize a conversation."""
    from services.context_budget import get_budget

    budget = get_budget("llama")
    trimmed = truncate_messages(messages, max_tokens=budget - 500)

    conversation_text = "\n".join(
        f"{m.get('role', 'user')}: {m.get('content', '')}" for m in trimmed
    )
    conversation_text = truncate_text(conversation_text, max_tokens=budget - 300)

    provider = config.get_llm_provider("llama")
    response = provider.chat(
        messages=[{"role": "user", "content": _SUMMARIZE_PROMPT + conversation_text}],
        system="You are a precise summarizer. Respond only with valid JSON.",
        max_tokens=512,
    )

    sid = session_id or str(uuid.uuid4())

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
) -> None:
    """Embed the session summary and store in Qdrant conversations collection."""
    embedder = config.get_embedder()
    vs = config.get_vector_store()

    embed_text = f"{summary.summary} Topics: {', '.join(summary.topics)}"
    vector = embedder.embed(embed_text)

    point_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"session::{summary.session_id}"))
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
        },
    )

    _log.info(
        "Stored session %s: %d topics, %d entities",
        summary.session_id,
        len(summary.topics),
        len(summary.entities),
    )
    hooks.fire_background(
        Event.SESSION_ENDED,
        session_id=summary.session_id,
        summary=summary.summary,
        topics=summary.topics,
        entities=summary.entities,
    )


def retrieve_context(
    query: str,
    limit: int = 3,
    user_id: str = "default",
) -> list[ContextHit]:
    """Search past session summaries for relevant context."""
    embedder = config.get_embedder()
    vs = config.get_vector_store()

    query_vec = embedder.embed(query)
    user_filter = {"must": [{"key": "user_id", "match": {"value": user_id}}]}
    raw = vs.search(
        collection="conversations",
        vector=query_vec,
        limit=limit,
        threshold=0.40,
        filter=user_filter,
    )

    return [
        ContextHit(
            session_id=r["payload"].get("session_id", ""),
            summary=r["payload"].get("summary", ""),
            score=r["score"],
        )
        for r in raw
    ]
