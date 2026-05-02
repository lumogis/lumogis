# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Chat completions + model listing for the v1 web façade.

Wraps :func:`loop.ask_stream` with a client-shaped DTO contract. SSE
chunk shape is identical to the existing OpenAI-shaped
``/v1/chat/completions`` route — the web client reuses the same parser
on both surfaces, so this module deliberately delegates to
:func:`routes.chat.stream_completion` to avoid drift.

Shipped behaviour pinned by the plan:

* ``messages[-1].content`` becomes ``question``; the rest becomes ``history``.
* ``user_id`` (keyword-only on :func:`loop.ask_stream`) is sourced from
  :func:`auth.get_user` so per-user LLM provider keys + per-user audit
  attribution work end-to-end.
* Last-message-is-user / system-message-position rules are enforced
  here so the SPA gets a deterministic 400 instead of a confusing 500
  later in the LLM stack.
"""

from __future__ import annotations

import logging
import os
from typing import Any
from uuid import uuid4

from auth import get_user
from authz import require_user
from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException
from fastapi import Request
from fastapi import status
from fastapi.responses import StreamingResponse
from loop import ask
from loop import ask_stream
from models.api_v1 import ChatCompletionRequest
from models.api_v1 import ChatCompletionResponse
from models.api_v1 import ChatMessageDTO
from models.api_v1 import ModelDescriptor
from models.api_v1 import ModelsResponse
from models.stream import StreamEvent
from routes.chat import should_prepend_local_loading_note
from routes.chat import stream_completion
from services.connector_credentials import ConnectorNotConfigured
from services.connector_credentials import CredentialUnavailable

import config

_log = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1",
    tags=["v1-chat"],
    dependencies=[Depends(require_user)],
)


def _validate_messages(messages: list[ChatMessageDTO]) -> None:
    """Raise 400 with stable error codes when the message order is wrong.

    The SPA's chat reducer guarantees these invariants, but the server
    enforces them so MCP clients / integration tests get the same
    contract.
    """
    if messages[-1].role != "user":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="last_message_must_be_user",
        )
    if not messages[-1].content.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="empty_message",
        )
    for idx, msg in enumerate(messages):
        if msg.role == "system" and idx != 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="system_message_position",
            )


def _split_messages(messages: list[ChatMessageDTO]) -> tuple[str, list[dict]]:
    """Return ``(question, history)`` in the shape :func:`loop.ask_stream` wants."""
    question = messages[-1].content
    history = [{"role": m.role, "content": m.content} for m in messages[:-1]]
    return question, history


def _rc_chat_stub_enabled() -> bool:
    """Test-only streaming stub (RC compose). Does not replace real LLM output."""
    raw = os.environ.get("LUMOGIS_RC_CHAT_STUB", "").strip().lower()
    return raw in ("1", "true", "yes")


def _rc_chat_stub_reply() -> str:
    body = os.environ.get("LUMOGIS_RC_CHAT_STUB_REPLY", "RC_CHAT_STUB_ACK").strip()
    return body if body else "RC_CHAT_STUB_ACK"


@router.post("/chat/completions")
def chat_completions(body: ChatCompletionRequest, request: Request) -> Any:
    user_id = get_user(request).user_id

    _validate_messages(body.messages)

    if not config.is_model_enabled(body.model, user_id=user_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"invalid_model:{body.model}",
        )

    question, history = _split_messages(body.messages)
    use_tools = config.get_model_config(body.model).get("tools", False)

    if body.stream:
        if _rc_chat_stub_enabled():

            def _stub_events():
                yield StreamEvent(type="text", content=_rc_chat_stub_reply())

            return StreamingResponse(
                stream_completion(
                    _stub_events(),
                    body.model,
                    prepend_loading_note=False,
                ),
                media_type="text/event-stream",
            )

        # Synchronous credential pre-flight — see the parallel comment in
        # ``routes/chat.py``. Doing this lazily inside the SSE generator
        # leaks credential errors out as HTTP 200 + text/event-stream.
        try:
            config.get_llm_provider(body.model, user_id=user_id)
        except ConnectorNotConfigured as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"error": "llm_provider_unavailable", "model": body.model},
            ) from exc
        except CredentialUnavailable as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"error": "llm_provider_key_missing", "model": body.model},
            ) from exc
        except Exception as exc:  # noqa: BLE001 — chat hot path, must surface
            _log.exception(
                "api_v1.chat.stream pre-flight failed model=%s user=%s",
                body.model,
                user_id,
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"error": "llm_provider_unavailable", "model": body.model},
            ) from exc

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
    except ConnectorNotConfigured as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": "llm_provider_unavailable", "model": body.model},
        ) from exc
    except CredentialUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": "llm_provider_key_missing", "model": body.model},
        ) from exc
    except Exception as exc:  # noqa: BLE001
        _log.exception(
            "api_v1.chat.completions failed model=%s user=%s",
            body.model,
            user_id,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": "llm_provider_unavailable", "model": body.model},
        ) from exc

    return ChatCompletionResponse(
        id=f"chatcmpl-{uuid4().hex[:12]}",
        model=body.model,
        message=ChatMessageDTO(role="assistant", content=answer),
        finished_at=_utcnow(),
    )


def _utcnow():
    from datetime import datetime
    from datetime import timezone

    return datetime.now(timezone.utc)


@router.get("/models", response_model=ModelsResponse)
def list_models(request: Request) -> ModelsResponse:
    user_id = get_user(request).user_id
    raw = config.get_all_models_config()
    descriptors: list[ModelDescriptor] = []
    for name, cfg in raw.items():
        provider = (cfg.get("provider") or _provider_from_base_url(cfg)).lower()
        descriptors.append(
            ModelDescriptor(
                id=name,
                label=cfg.get("label") or name,
                is_local=config.is_local_model(name),
                enabled=config.is_model_enabled(name, user_id=user_id),
                provider=provider,
            )
        )
    descriptors.sort(key=lambda m: (not m.enabled, m.label.lower()))
    return ModelsResponse(models=descriptors)


def _provider_from_base_url(cfg: dict) -> str:
    """Best-effort provider tag for a YAML row that omits ``provider``."""
    base = (cfg.get("base_url") or "").lower()
    if "ollama" in base:
        return "ollama"
    if "anthropic" in base:
        return "anthropic"
    if "openai" in base:
        return "openai"
    return "unknown"
