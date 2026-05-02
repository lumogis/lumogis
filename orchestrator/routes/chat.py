# SPDX-License-Identifier: AGPL-3.0-only
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
from auth import UserContext
from auth import auth_enabled
from auth import get_user
from authz import require_user
from events import Event
from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException
from fastapi import Request
from fastapi.responses import JSONResponse
from fastapi.responses import StreamingResponse
from loop import ask
from loop import ask_stream
from models.stream import StreamEvent
from pydantic import BaseModel
from services.connector_credentials import ConnectorNotConfigured
from services.connector_credentials import CredentialUnavailable
from services.context_budget import allocate
from services.context_budget import get_budget
from services.context_budget import truncate_messages
from services.context_budget import truncate_text
from services.llm_connector_map import connector_for_api_key_env
from services.llm_connector_map import get_user_credentials_snapshot
from services.llm_connector_map import vendor_label_for_connector

import config

router = APIRouter()
_log = logging.getLogger(__name__)


@router.get("/v1/models")
def list_models(request: Request):
    """OpenAI-compatible model list — only returns enabled models.

    Plan llm_provider_keys_per_user_migration Pass 2.8: under
    ``AUTH_ENABLED=true`` the response is filtered per-user (cloud models
    only show up when the caller has a row in ``user_connector_credentials``
    for the matching ``llm_*`` connector). Auth-off keeps legacy behaviour.
    Per-request memoisation: one ``SELECT`` against
    ``user_connector_credentials`` for the entire response, regardless of
    cloud-model count (see ``services.llm_connector_map.get_user_credentials_snapshot``).
    """
    all_models = config.get_all_models_config()
    if auth_enabled():
        user_id = get_user(request).user_id
        present = get_user_credentials_snapshot(user_id)
        data = [
            {"id": name, "object": "model", "owned_by": "lumogis"}
            for name in all_models
            if config.is_model_enabled(name, user_id=user_id, _credentials_present=present)
        ]
    else:
        data = [
            {"id": name, "object": "model", "owned_by": "lumogis"}
            for name in all_models
            if config.is_model_enabled(name)
        ]
    return {"object": "list", "data": data}


def _vendor_label_for_model(model_name: str) -> str:
    """Return the human vendor label for a model's ``api_key_env`` (best-effort)."""
    try:
        cfg = config.get_model_config(model_name)
    except Exception:
        return model_name
    api_key_env = cfg.get("api_key_env")
    if not api_key_env:
        return model_name
    connector = connector_for_api_key_env(api_key_env)
    if not connector:
        return model_name
    return vendor_label_for_connector(connector)


def _connector_not_configured_response(model: str) -> JSONResponse:
    vendor = _vendor_label_for_model(model)
    return JSONResponse(
        status_code=424,
        content={
            "error": {
                "code": "connector_not_configured",
                "message": (
                    f"{vendor} API key not configured for this user. "
                    "Set it in dashboard \u2192 My LLM keys."
                ),
                "model": model,
                "type": "invalid_request_error",
            }
        },
    )


def _credential_unavailable_response(model: str) -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content={
            "error": {
                "code": "credential_unavailable",
                "message": "Stored credential could not be decrypted.",
                "model": model,
                "type": "server_error",
            }
        },
    )


def _internal_credential_error_response(model: str) -> JSONResponse:
    return JSONResponse(
        status_code=500,
        content={
            "error": {
                "code": "internal_error",
                "message": "Internal error resolving credential.",
                "model": model,
                "type": "server_error",
            }
        },
    )


class AskRequest(BaseModel):
    text: str


class AskResponse(BaseModel):
    answer: str


@router.post("/ask", response_model=AskResponse)
def ask_endpoint(body: AskRequest, user: UserContext = Depends(require_user)) -> AskResponse:
    answer = ask(body.text, history=[], user_id=user.user_id)
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

    # When the graph runs as an out-of-process service, the in-process
    # CONTEXT_BUILDING listener is gone (the plugin self-disables in non-
    # `inprocess` modes). Issue a synchronous /context HTTP call to the KG
    # service to obtain the same fragments. The 40 ms hard timeout lives
    # inside `get_context_sync`; on timeout / KG-down it returns [], so this
    # extension never blocks the chat reply for more than the budget. The
    # CONTEXT_BUILDING event still fires above for any other subscribers
    # (today there are none besides the graph plugin, but this preserves the
    # contract). `config.get_graph_mode()` is `@cache`-decorated so this is
    # a dict lookup, not an env-var read, on the chat hot path.
    if config.get_graph_mode() == "service":
        from services.graph_webhook_dispatcher import get_context_sync

        graph_fragments = get_context_sync(
            query=question,
            user_id=user_id,
            max_fragments=3,
        )
        fragments.extend(graph_fragments)

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

    # Plan llm_provider_keys_per_user_migration Pass 2.8: resolve user_id
    # FIRST so the per-user is_model_enabled call below sees the right
    # credential context. Under auth-off, get_user returns the legacy default
    # user; under auth-on, missing/invalid auth raises 401 here.
    user_id = get_user(request).user_id

    if not config.is_model_enabled(body.model, user_id=user_id):
        raise HTTPException(
            status_code=404,
            detail=f"Model '{body.model}' is not available. "
            "Enable it in Settings and provide an API key, or choose another model.",
        )

    last = body.messages[-1]
    question = _content_to_str(last.content)
    history = []
    for m in body.messages[:-1]:
        text = _content_to_str(m.content)
        history.append({"role": m.role, "content": text})
    use_tools = config.get_model_config(body.model).get("tools", False)

    history = _inject_context(question, history, body.model, user_id)

    if body.stream:
        # Synchronous credential pre-flight — see plan §Modified files
        # routes/chat.py + §Test cases test_chat_completions_424_streaming_returns_json_not_sse:
        # loop.ask_stream wraps get_llm_provider in a broad except that yields
        # SSE error events; if we let it resolve credentials lazily, a
        # ConnectorNotConfigured/CredentialUnavailable would be smuggled out
        # as HTTP 200 + text/event-stream instead of the documented 424/503.
        # Once StreamingResponse is constructed the status code/headers are
        # locked, so the pre-flight MUST run before that.
        try:
            config.get_llm_provider(body.model, user_id=user_id)
        except ConnectorNotConfigured:
            return _connector_not_configured_response(body.model)
        except CredentialUnavailable:
            return _credential_unavailable_response(body.model)
        except Exception:
            _log.exception(
                "chat.stream pre-flight failed for model=%s user=%s",
                body.model,
                user_id,
            )
            return _internal_credential_error_response(body.model)

        events = ask_stream(
            question,
            history=history,
            model=body.model,
            use_tools=use_tools,
            user_id=user_id,
        )
        return StreamingResponse(
            stream_completion(
                events,
                body.model,
                prepend_loading_note=should_prepend_local_loading_note(body.model),
            ),
            media_type="text/event-stream",
        )

    try:
        answer = ask(
            question,
            history=history,
            model=body.model,
            use_tools=use_tools,
            user_id=user_id,
        )
    except ConnectorNotConfigured:
        return _connector_not_configured_response(body.model)
    except CredentialUnavailable:
        return _credential_unavailable_response(body.model)
    except Exception:
        _log.exception(
            "chat.completions failed for model=%s user=%s",
            body.model,
            user_id,
        )
        return _internal_credential_error_response(body.model)

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
