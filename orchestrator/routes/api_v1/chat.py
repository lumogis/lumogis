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
from models.api_v1 import DocumentCitationDTO
from models.api_v1 import LumogisChatExtensions
from models.api_v1 import ModelDescriptor
from models.api_v1 import ModelsResponse
from routes.chat import _privacy_mode_blocked_response
from routes.chat import build_injected_context
from routes.chat import should_prepend_local_loading_note
from routes.chat import stream_completion
from services.connector_credentials import ConnectorNotConfigured
from services.connector_credentials import CredentialUnavailable
from services.document_scope import DocumentNotFoundError
from services.document_scope import resolve_document_file_path

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


def _citations_to_dto(citations) -> list[DocumentCitationDTO]:
    return [
        DocumentCitationDTO(
            chunk_index=c.chunk_index,
            file_path=c.file_path,
            score=c.score,
            score_kind=c.score_kind,
        )
        for c in citations
    ]


def _resolve_scoped_injection(
    body: ChatCompletionRequest,
    request: Request,
    *,
    chat_model: str,
) -> tuple[str, list[dict], set[str], list[DocumentCitationDTO], bool]:
    """When document_id is set, resolve path and inject scoped context."""
    user = get_user(request)
    if body.document_id is not None and body.document_id <= 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": "invalid_document_id"},
        )

    question, history = _split_messages(body.messages)
    use_tools = False

    if body.document_id is None:
        use_tools = config.get_model_config(chat_model).get("tools", False)
        auto_rag_point_ids: set[str] = set()
        try:
            injection = build_injected_context(
                question,
                history,
                chat_model,
                user.user_id,
                auto_rag_point_ids=auto_rag_point_ids,
            )
        except Exception:
            _log.warning(
                "api_v1.chat unscoped context injection failed user=%s",
                user.user_id,
                exc_info=True,
            )
            return question, history, set(), [], use_tools
        return question, injection.messages, auto_rag_point_ids, [], use_tools

    try:
        scoped_path = resolve_document_file_path(user, body.document_id)
    except DocumentNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "document_not_found"},
        ) from exc

    auto_rag_point_ids: set[str] = set()
    try:
        injection = build_injected_context(
            question,
            history,
            chat_model,
            user.user_id,
            auto_rag_point_ids=auto_rag_point_ids,
            scoped_file_path=scoped_path,
        )
    except Exception as exc:
        _log.warning(
            "scoped document chat retrieval failed document_id=%s user=%s",
            body.document_id,
            user.user_id,
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": "auto_rag_failed"},
        ) from exc

    if not injection.citations:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error": "document_context_unavailable",
                "message": "No grounded chunks available for the pinned document.",
            },
        )

    citation_dtos = _citations_to_dto(injection.citations)
    return question, injection.messages, auto_rag_point_ids, citation_dtos, use_tools


@router.post("/chat/completions")
def chat_completions(body: ChatCompletionRequest, request: Request) -> Any:
    from services.privacy_mode import PrivacyModeBlocked
    from services.privacy_mode import blocks_remote_models
    from services.privacy_mode import resolve_model_for_request

    user_id = get_user(request).user_id

    _validate_messages(body.messages)

    blocks_remote = blocks_remote_models(user_id)
    try:
        effective_model, privacy_meta = resolve_model_for_request(body.model, user_id)
    except PrivacyModeBlocked:
        return _privacy_mode_blocked_response(body.model)

    if not config.is_model_enabled(
        effective_model,
        user_id=user_id,
        _privacy_blocks_remote=blocks_remote,
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"invalid_model:{effective_model}",
        )

    question, history, auto_rag_point_ids, citation_dtos, use_tools = _resolve_scoped_injection(
        body, request, chat_model=effective_model
    )

    if body.stream:
        try:
            config.get_llm_provider(effective_model, user_id=user_id)
        except PrivacyModeBlocked:
            return _privacy_mode_blocked_response(body.model)
        except ConnectorNotConfigured as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"error": "llm_provider_unavailable", "model": effective_model},
            ) from exc
        except CredentialUnavailable as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"error": "llm_provider_key_missing", "model": effective_model},
            ) from exc
        except Exception as exc:  # noqa: BLE001 — chat hot path, must surface
            from services.egress_guard import EgressBlockedError

            if isinstance(exc, EgressBlockedError):
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail={"error": "egress_blocked", "model": effective_model},
                ) from exc
            _log.exception(
                "api_v1.chat.stream pre-flight failed model=%s user=%s",
                effective_model,
                user_id,
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"error": "llm_provider_unavailable", "model": effective_model},
            ) from exc

        events = ask_stream(
            question,
            history=history,
            model=effective_model,
            use_tools=use_tools,
            user_id=user_id,
            auto_rag_point_ids=auto_rag_point_ids or None,
        )
        from models.context_injection import DocumentCitation

        context_citations = (
            [
                DocumentCitation(
                    chunk_index=c.chunk_index,
                    file_path=c.file_path,
                    score=c.score,
                    score_kind=c.score_kind,
                )
                for c in citation_dtos
            ]
            if citation_dtos
            else None
        )

        return StreamingResponse(
            stream_completion(
                events,
                effective_model,
                prepend_loading_note=should_prepend_local_loading_note(effective_model),
                context_citations=context_citations,
                privacy_metadata=privacy_meta,
            ),
            media_type="text/event-stream",
        )

    try:
        answer = ask(
            question,
            history=history,
            model=effective_model,
            use_tools=use_tools,
            user_id=user_id,
            auto_rag_point_ids=auto_rag_point_ids or None,
        )
    except ConnectorNotConfigured as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": "llm_provider_unavailable", "model": effective_model},
        ) from exc
    except CredentialUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": "llm_provider_key_missing", "model": effective_model},
        ) from exc
    except Exception as exc:  # noqa: BLE001
        from services.egress_guard import EgressBlockedError

        if isinstance(exc, EgressBlockedError):
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"error": "egress_blocked", "model": effective_model},
            ) from exc
        _log.exception(
            "api_v1.chat.completions failed model=%s user=%s",
            effective_model,
            user_id,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": "llm_provider_unavailable", "model": effective_model},
        ) from exc

    lumogis = None
    if citation_dtos or privacy_meta:
        lumogis = LumogisChatExtensions(
            context_citations=citation_dtos or [],
            privacy=privacy_meta,
        )

    return ChatCompletionResponse(
        id=f"chatcmpl-{uuid4().hex[:12]}",
        model=effective_model,
        message=ChatMessageDTO(role="assistant", content=answer),
        finished_at=_utcnow(),
        lumogis=lumogis,
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
