# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Conversation history API — browse, continue, delete (LUM-162)."""

from __future__ import annotations

import uuid

from auth import UserContext
from authz import require_user
from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException
from fastapi import status
from models.api_v1 import ConversationContinueRequest
from models.api_v1 import ConversationContinueResponse
from models.api_v1 import ConversationCreateRequest
from models.api_v1 import ConversationDeleteResponse
from models.api_v1 import ConversationDetail
from models.api_v1 import ConversationListResponse
from models.api_v1 import ConversationMessage
from models.api_v1 import ConversationMessageAppendRequest
from models.api_v1 import ConversationPatchRequest
from models.api_v1 import ConversationSummary
from services.conversations import ConversationNotFoundError

from services import conversations as conv_svc

router = APIRouter(
    prefix="/api/v1/conversations",
    tags=["v1-conversations"],
    dependencies=[Depends(require_user)],
)


def _parse_conversation_id(conversation_id: str) -> str:
    try:
        return str(uuid.UUID(conversation_id))
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": "invalid_conversation_id"},
        ) from exc


@router.get("", response_model=ConversationListResponse)
def list_conversations(
    user: UserContext = Depends(require_user),
    limit: int = 50,
) -> ConversationListResponse:
    rows = conv_svc.list_conversations(user.user_id, limit=limit)
    return ConversationListResponse(conversations=rows)


@router.post("", response_model=ConversationSummary, status_code=status.HTTP_201_CREATED)
def create_conversation(
    body: ConversationCreateRequest,
    user: UserContext = Depends(require_user),
) -> ConversationSummary:
    return conv_svc.create_web_conversation(
        user_id=user.user_id,
        title=body.title,
        model=body.model,
    )


@router.get("/{conversation_id}", response_model=ConversationDetail)
def get_conversation(
    conversation_id: str,
    user: UserContext = Depends(require_user),
) -> ConversationDetail:
    cid = _parse_conversation_id(conversation_id)
    try:
        return conv_svc.get_conversation(user.user_id, cid)
    except ConversationNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "conversation_not_found"},
        ) from exc


@router.put("/{conversation_id}", response_model=ConversationSummary)
def patch_conversation(
    conversation_id: str,
    body: ConversationPatchRequest,
    user: UserContext = Depends(require_user),
) -> ConversationSummary:
    cid = _parse_conversation_id(conversation_id)
    try:
        return conv_svc.update_web_conversation(
            user_id=user.user_id,
            conversation_id=cid,
            title=body.title,
            model=body.model,
        )
    except ConversationNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "conversation_not_found"},
        ) from exc


@router.delete("/{conversation_id}", response_model=ConversationDeleteResponse)
def delete_conversation(
    conversation_id: str,
    user: UserContext = Depends(require_user),
) -> ConversationDeleteResponse:
    cid = _parse_conversation_id(conversation_id)
    try:
        result = conv_svc.delete_conversation(user.user_id, cid)
    except ConversationNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "conversation_not_found"},
        ) from exc
    return ConversationDeleteResponse(
        deleted=True,
        conversation_id=cid,
        partial=result.partial,
    )


@router.post("/{conversation_id}/continue", response_model=ConversationContinueResponse)
def continue_conversation(
    conversation_id: str,
    body: ConversationContinueRequest | None = None,
    user: UserContext = Depends(require_user),
) -> ConversationContinueResponse:
    cid = _parse_conversation_id(conversation_id)
    try:
        detail = conv_svc.get_conversation(user.user_id, cid)
    except ConversationNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "conversation_not_found"},
        ) from exc
    seed = conv_svc.build_continue_seed(detail)
    return ConversationContinueResponse(seed_messages=seed)


@router.post(
    "/{conversation_id}/messages",
    response_model=ConversationMessage,
    status_code=status.HTTP_201_CREATED,
)
def append_message(
    conversation_id: str,
    body: ConversationMessageAppendRequest,
    user: UserContext = Depends(require_user),
) -> ConversationMessage:
    cid = _parse_conversation_id(conversation_id)
    mid = _parse_conversation_id(body.message_id)
    try:
        return conv_svc.append_web_message(
            user_id=user.user_id,
            conversation_id=cid,
            message_id=mid,
            role=body.role,
            content=body.content,
            model=body.model,
        )
    except ConversationNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "conversation_not_found"},
        ) from exc
