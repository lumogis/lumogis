# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Batch handler: session end processing (summary, entities, store, hooks)."""

from __future__ import annotations

from models.sessions import SessionEndPayload
from services.batch_queue import register_batch_handler
from services.entities import extract_entities
from services.entities import store_entities
from services.memory import store_session
from services.memory import summarize_session


@register_batch_handler("session_end", SessionEndPayload)
def handle(*, user_id: str, payload: SessionEndPayload) -> None:
    import hooks
    from events import Event

    msg_dicts = [{"role": m.role, "content": m.content} for m in payload.messages]
    summary = summarize_session(
        msg_dicts, session_id=payload.session_id, user_id=user_id
    )

    session_text = "\n".join(f"{m['role']}: {m['content']}" for m in msg_dicts)
    entities = extract_entities(session_text, user_id=user_id)
    entity_ids = store_entities(
        entities,
        evidence_id=payload.session_id,
        evidence_type="SESSION",
        user_id=user_id,
    )

    store_session(summary, user_id=user_id, entity_ids=entity_ids)

    hooks.fire_background(
        Event.SESSION_ENDED,
        session_id=summary.session_id,
        summary=summary.summary,
        topics=summary.topics,
        entities=summary.entities,
        entity_ids=entity_ids,
        user_id=user_id,
    )
