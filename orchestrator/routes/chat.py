# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Lumogis
"""Chat endpoints: /ask and /v1/chat/completions."""

import json
import logging
import time
from typing import Any
from typing import Generator
from typing import List
from typing import Optional

import hooks
from auth import get_user
from events import Event
from fastapi import APIRouter
from fastapi import Request
from fastapi.responses import StreamingResponse
from loop import ask
from loop import ask_stream
from models.stream import StreamEvent
from pydantic import BaseModel
from services.context_budget import allocate
from services.context_budget import get_budget
from services.context_budget import truncate_messages
from services.context_budget import truncate_text

import config

router = APIRouter()
_log = logging.getLogger(__name__)


@router.get("/v1/models")
def list_models():
    """OpenAI-compatible model list — only returns enabled models."""
    all_models = config.get_all_models_config()
    data = [
        {"id": name, "object": "model", "owned_by": "lumogis"}
        for name in all_models
        if config.is_model_enabled(name)
    ]
    return {"object": "list", "data": data}


class AskRequest(BaseModel):
    text: str


class AskResponse(BaseModel):
    answer: str


@router.post("/ask", response_model=AskResponse)
def ask_endpoint(body: AskRequest) -> AskResponse:
    answer = ask(body.text, history=[])
    return AskResponse(answer=answer)


class ChatMessage(BaseModel):
    role: str
    content: Optional[str | List[Any]] = None


class ChatCompletionsRequest(BaseModel):
    model: str = "claude"
    messages: List[ChatMessage]
    stream: bool = False


def _content_to_str(content: Optional[str | List[Any]]) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    parts = []
    for part in content:
        if isinstance(part, dict) and part.get("type") == "text":
            parts.append(part.get("text", ""))
        elif isinstance(part, str):
            parts.append(part)
    return "".join(parts)


def _sse_chunk(
    chunk_id: str,
    created: int,
    model: str,
    delta: dict,
    finish: str | None,
) -> str:
    payload = {
        "id": chunk_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }
    return f"data: {json.dumps(payload)}\n\n"


LOCAL_MODEL_LOADING_NOTE = "*Loading model on your machine — first time may take 1–2 minutes…*\n\n"

# Shown at most once per orchestrator process per local model (not every chat turn).
_local_model_loading_note_shown: set[str] = set()


def should_prepend_local_loading_note(model: str) -> bool:
    """True once per process for each local model; avoids repeating the hint every message."""
    if not config.is_local_model(model):
        return False
    if model in _local_model_loading_note_shown:
        return False
    _local_model_loading_note_shown.add(model)
    return True


def stream_completion(
    events: Generator[StreamEvent, None, None],
    model: str,
    *,
    prepend_loading_note: bool = False,
) -> Generator[str, None, None]:
    cid = "chatcmpl-lumogis"
    created = int(time.time())
    yield _sse_chunk(cid, created, model, {"role": "assistant", "content": ""}, None)
    if prepend_loading_note:
        yield _sse_chunk(cid, created, model, {"content": LOCAL_MODEL_LOADING_NOTE}, None)
    for event in events:
        if event.type in ("text", "error"):
            yield _sse_chunk(cid, created, model, {"content": event.content}, None)
    yield _sse_chunk(cid, created, model, {}, "stop")
    yield "data: [DONE]\n\n"


def _inject_context(question: str, history: list[dict], model: str, user_id: str) -> list[dict]:
    """Retrieve session memory and plugin context, then budget-trim history."""
    from services.memory import retrieve_context

    budget = get_budget(model)
    budget_plan = allocate(
        budget,
        {
            "system": 0.10,
            "session_context": 0.075,
            "plugin_context": 0.05,
            "history": 0.65,
            "response": 0.125,
        },
    )

    context_parts: list[str] = []

    hits = retrieve_context(question, limit=3, user_id=user_id)
    if hits:
        session_texts = [f"[Previous session] {h.summary}" for h in hits]
        session_block = "\n".join(session_texts)
        session_block = truncate_text(session_block, budget_plan.get("session_context"))
        context_parts.append(session_block)

    fragments: list[str] = list(context_parts)
    hooks.fire(Event.CONTEXT_BUILDING, query=question, context_fragments=fragments)

    if len(fragments) > len(context_parts):
        plugin_text = "\n".join(fragments[len(context_parts) :])
        plugin_text = truncate_text(plugin_text, budget_plan.get("plugin_context"))
        context_parts.append(plugin_text)

    history_budget = budget_plan.get("history")
    trimmed_history = truncate_messages(history, max_tokens=history_budget)

    if context_parts:
        context_block = "\n\n".join(context_parts)
        context_msg = {
            "role": "user",
            "content": (
                "[Context from previous sessions "
                "— use this to inform your answer]"
                f"\n{context_block}"
            ),
        }
        ack_msg = {
            "role": "assistant",
            "content": "Understood. I'll use this context to inform my responses.",
        }
        return [context_msg, ack_msg] + trimmed_history
    return trimmed_history


@router.post("/v1/chat/completions")
def chat_completions(body: ChatCompletionsRequest, request: Request) -> Any:
    if not body.messages:
        if body.stream:
            return StreamingResponse(
                stream_completion(iter([]), body.model),
                media_type="text/event-stream",
            )
        return {
            "id": "chatcmpl-lumogis",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": body.model,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": ""},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        }

    if not config.is_model_enabled(body.model):
        from fastapi import HTTPException
        raise HTTPException(
            status_code=404,
            detail=f"Model '{body.model}' is not available. "
                   "Enable it in Settings and provide an API key, or choose another model.",
        )

    user_id = get_user(request).user_id

    last = body.messages[-1]
    question = _content_to_str(last.content)
    history = []
    for m in body.messages[:-1]:
        text = _content_to_str(m.content)
        history.append({"role": m.role, "content": text})
    use_tools = config.get_model_config(body.model).get("tools", False)

    history = _inject_context(question, history, body.model, user_id)

    if body.stream:
        events = ask_stream(
            question,
            history=history,
            model=body.model,
            use_tools=use_tools,
        )
        return StreamingResponse(
            stream_completion(
                events,
                body.model,
                prepend_loading_note=should_prepend_local_loading_note(body.model),
            ),
            media_type="text/event-stream",
        )

    answer = ask(question, history=history, model=body.model, use_tools=use_tools)
    return {
        "id": "chatcmpl-lumogis",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": body.model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": answer},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }
