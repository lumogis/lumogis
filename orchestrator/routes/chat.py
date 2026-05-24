# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Chat endpoints: /ask and /v1/chat/completions."""

import json
import logging
import time
import uuid
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
from models.memory import DocumentContextHit
from models.stream import StreamEvent
from pydantic import BaseModel
from services.connector_credentials import ConnectorNotConfigured
from services.connector_credentials import CredentialUnavailable
from services.context_budget import allocate
from services.context_budget import estimate_tokens
from services.context_budget import get_budget
from services.context_budget import truncate_messages
from services.context_budget import truncate_text
from services.injection_sanitiser import ResolvedOrigin
from services.injection_sanitiser import apply_retrieved_chunk_markup
from services.injection_sanitiser import assistant_nonce_acknowledgement
from services.injection_sanitiser import build_outer_injected_bundle
from services.injection_sanitiser import sanitize_attribute_source_token
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


def _fit_plaintext_bundle(
    fragments: list[str],
    hints: list[ResolvedOrigin | None],
    max_tokens: int,
) -> tuple[list[str], list[ResolvedOrigin | None]]:
    """Drop plaintext fragments from the tail until corpus fits approximate tokens."""

    fr = list(fragments)
    h = list(hints)
    if max_tokens <= 0:
        return [], []

    while len(h) < len(fr):
        h.append(None)
    while len(h) > len(fr):
        h.pop()

    while fr and sum(estimate_tokens(chunk) for chunk in fr if chunk) > max_tokens:
        fr.pop()
        if h:
            h.pop()

    return fr, h


def _resolved_document_origin(hit: DocumentContextHit) -> ResolvedOrigin:
    from datetime import datetime
    from datetime import timezone

    iso_stamp = hit.ingested or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    scope_val = hit.scope if hit.scope in ("personal", "shared", "system") else "personal"
    token = sanitize_attribute_source_token(f"document:{hit.file_path}")
    return {
        "trusted": False,
        "scope": scope_val,
        "source": token,
        "session_id": None,
        "ingested": iso_stamp,
        "pattern_hits": [],
    }


def _inject_context(
    question: str,
    history: list[dict],
    model: str,
    user_id: str,
    *,
    auto_rag_point_ids: set[str] | None = None,
) -> list[dict]:
    """Retrieve session memory / graph snippets, annotate corpus, trim history."""
    from datetime import datetime
    from datetime import timezone

    from services.auto_rag import retrieve_document_context
    from services.memory import retrieve_context

    def _resolved_session_origin(hit_scope: str, session_sid: str) -> ResolvedOrigin:
        iso_stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        scope_val = hit_scope if hit_scope in ("personal", "shared", "system") else "personal"
        token = sanitize_attribute_source_token(f"session_memory:{session_sid or 'anonymous'}")
        return {
            "trusted": False,
            "scope": scope_val,
            "source": token,
            "session_id": session_sid or None,
            "ingested": iso_stamp,
            "pattern_hits": [],
        }

    budget = get_budget(model)
    budget_plan = allocate(
        budget,
        {
            "system": 0.10,
            "session_context": 0.075,
            "entities": 0.05,
            "plugin_context": 0.02,
            "history": 0.58,
            "documents": 0.05,
            "response": 0.125,
        },
    )

    fragments_plain: list[str] = []
    origin_hints: list[ResolvedOrigin | None] = []

    hits = retrieve_context(question, limit=3, user_id=user_id)
    for hit in hits:
        stripped = hit.summary.strip()
        if stripped:
            fragments_plain.append(stripped)
            origin_hints.append(_resolved_session_origin(hit.scope, hit.session_id))

    n_session = len(fragments_plain)

    est_window = budget
    doc_fraction = 0.05
    documents_slot_tokens = min(
        config.get_auto_rag_max_tokens(),
        int(doc_fraction * est_window),
    )
    doc_hits = retrieve_document_context(question, user_id, max_tokens=documents_slot_tokens)
    for hit in doc_hits:
        stripped = hit.chunk_text.strip()
        if not stripped:
            continue
        if auto_rag_point_ids is not None and hit.point_id:
            auto_rag_point_ids.add(hit.point_id)
        fragments_plain.append(stripped)
        origin_hints.append(_resolved_document_origin(hit))

    n_doc = len(fragments_plain) - n_session

    documents_budget = budget_plan.get("documents")
    if n_doc > 0 and documents_budget > 0:
        per_line = max(8, documents_budget // n_doc)
        for i in range(n_session, n_session + n_doc):
            fragments_plain[i] = truncate_text(fragments_plain[i], per_line)

    # Core ↔ KG default: never cap HTTP /context below the configured entity budget
    # (bounded by ContextRequest.max_fragments le=20).
    _max_ctx_fragments = min(config.get_context_entity_budget(), 20)

    hooks.fire(
        Event.CONTEXT_BUILDING,
        query=question,
        context_fragments=fragments_plain,
        user_id=user_id,
    )

    while len(origin_hints) < len(fragments_plain):
        origin_hints.append(None)

    if config.get_graph_mode() == "service":
        try:
            from services.graph_webhook_dispatcher import get_context_sync
        except ImportError as exc:
            _log.warning(
                "Chat context augmentation skipped — graph webhook dispatcher unavailable",
                extra={
                    "event": "chat_context_augmentation_skipped",
                    "reason": "context_sync_import_error",
                    "exc_type": type(exc).__name__,
                },
            )
        else:
            graph_fragments = get_context_sync(
                query=question,
                user_id=user_id,
                max_fragments=_max_ctx_fragments,
            )
            for frag_line in graph_fragments:
                fragments_plain.append(frag_line)
                origin_hints.append(None)

    session_budget = budget_plan.get("session_context")
    entities_budget = budget_plan.get("entities")
    sess_frags = fragments_plain[:n_session]
    sess_hints = origin_hints[:n_session]
    doc_frags = fragments_plain[n_session : n_session + n_doc]
    doc_hints = origin_hints[n_session : n_session + n_doc]
    graph_frags = fragments_plain[n_session + n_doc :]
    graph_hints = origin_hints[n_session + n_doc :]

    sess_frags, sess_hints = _fit_plaintext_bundle(sess_frags, sess_hints, session_budget)
    doc_frags, doc_hints = _fit_plaintext_bundle(doc_frags, doc_hints, documents_budget)
    graph_frags, graph_hints = _fit_plaintext_bundle(graph_frags, graph_hints, entities_budget)
    fragments_plain = sess_frags + doc_frags + graph_frags
    origin_hints = sess_hints + doc_hints + graph_hints

    history_budget = budget_plan.get("history")
    trimmed_history = truncate_messages(history, max_tokens=history_budget)

    if not fragments_plain:
        return trimmed_history

    if config.is_injection_sanitiser_enabled():
        apply_retrieved_chunk_markup(
            fragments_plain,
            origin_hints,
            user_id=user_id,
            query=question,
        )
        nonce_tail = uuid.uuid4().hex
        joined_inner = "\n\n".join(fragments_plain)
        outer = build_outer_injected_bundle(joined_inner, nonce=nonce_tail)
        context_msg = {
            "role": "user",
            "content": outer,
        }
        ack_msg = {
            "role": "assistant",
            "content": assistant_nonce_acknowledgement(nonce_tail),
        }
        return [context_msg, ack_msg] + trimmed_history

    joined_plain = "\n\n".join(fragments_plain)
    pooled_budget = max(
        96,
        budget_plan.get("session_context")
        + budget_plan.get("plugin_context")
        + budget_plan.get("entities")
        + budget_plan.get("documents"),
    )
    joined_plain = truncate_text(joined_plain, max_tokens=pooled_budget)
    context_msg = {
        "role": "user",
        "content": f"Retrieved excerpts for grounding:\n{joined_plain}",
    }
    ack_msg = {
        "role": "assistant",
        "content": "Acknowledged excerpts are reference-only scaffolding.",
    }
    return [context_msg, ack_msg] + trimmed_history


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

    auto_rag_point_ids: set[str] = set()
    history = _inject_context(
        question, history, body.model, user_id, auto_rag_point_ids=auto_rag_point_ids
    )

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
            auto_rag_point_ids.clear()
            return _connector_not_configured_response(body.model)
        except CredentialUnavailable:
            auto_rag_point_ids.clear()
            return _credential_unavailable_response(body.model)
        except Exception:
            _log.exception(
                "chat.stream pre-flight failed for model=%s user=%s",
                body.model,
                user_id,
            )
            auto_rag_point_ids.clear()
            return _internal_credential_error_response(body.model)

        events = ask_stream(
            question,
            history=history,
            model=body.model,
            use_tools=use_tools,
            user_id=user_id,
            auto_rag_point_ids=auto_rag_point_ids,
        )

        def _stream_body() -> Generator[str, None, None]:
            try:
                yield from stream_completion(
                    events,
                    body.model,
                    prepend_loading_note=should_prepend_local_loading_note(body.model),
                )
            finally:
                auto_rag_point_ids.clear()

        return StreamingResponse(
            _stream_body(),
            media_type="text/event-stream",
        )

    try:
        answer = ask(
            question,
            history=history,
            model=body.model,
            use_tools=use_tools,
            user_id=user_id,
            auto_rag_point_ids=auto_rag_point_ids,
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
    finally:
        auto_rag_point_ids.clear()

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
