# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Document library API — list, detail, delete, re-ingest (LUM-160)."""

from __future__ import annotations

from auth import UserContext
from authz import require_user
from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException
from fastapi import status
from models.api_v1 import DocumentDeleteResponse
from models.api_v1 import DocumentDetail
from models.api_v1 import DocumentListResponse
from models.api_v1 import ReingestQueuedResponse
from models.api_v1 import ReingestRequest
from models.api_v1 import ShareQueuedResponse
from services.document_purge import DocumentNotFoundError
from services.documents import DocumentNotSharedError
from services.documents import SourceUnavailableError

from fastapi import Body

from services import documents as doc_svc

router = APIRouter(
    prefix="/api/v1/documents",
    tags=["v1-documents"],
    dependencies=[Depends(require_user)],
)


def _parse_document_id(document_id: str) -> int:
    try:
        parsed = int(document_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": "invalid_document_id"},
        ) from exc
    if parsed <= 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": "invalid_document_id"},
        )
    return parsed


def _parse_limit(limit: int) -> int:
    if limit <= 0 or limit > 100:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": "invalid_limit"},
        )
    return limit


@router.get("", response_model=DocumentListResponse)
def list_documents(
    user: UserContext = Depends(require_user),
    limit: int = 50,
) -> DocumentListResponse:
    lim = _parse_limit(limit)
    rows = doc_svc.list_documents(user, limit=lim)
    return DocumentListResponse(documents=rows)


@router.get("/{document_id}", response_model=DocumentDetail)
def get_document(
    document_id: str,
    user: UserContext = Depends(require_user),
) -> DocumentDetail:
    doc_id = _parse_document_id(document_id)
    try:
        return doc_svc.get_document(user, doc_id)
    except DocumentNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "document_not_found"},
        ) from exc


@router.delete("/{document_id}", response_model=DocumentDeleteResponse)
def delete_document(
    document_id: str,
    user: UserContext = Depends(require_user),
) -> DocumentDeleteResponse:
    doc_id = _parse_document_id(document_id)
    try:
        result = doc_svc.delete_document(user.user_id, doc_id)
    except DocumentNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "document_not_found"},
        ) from exc
    return DocumentDeleteResponse(
        document_id=doc_id,
        deleted=result.postgres_deleted,
        partial=result.partial,
        errors=result.errors,
    )


@router.post(
    "/{document_id}/reingest",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=ReingestQueuedResponse,
)
def reingest_document(
    document_id: str,
    body: ReingestRequest | None = None,
    user: UserContext = Depends(require_user),
) -> ReingestQueuedResponse:
    doc_id = _parse_document_id(document_id)
    force = body.force if body is not None else False
    try:
        return doc_svc.reingest_document(user.user_id, doc_id, force=force)
    except DocumentNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "document_not_found"},
        ) from exc
    except SourceUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error": "source_unavailable"},
        ) from exc


@router.post(
    "/{document_id}/publish",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=ShareQueuedResponse,
)
def publish_document(
    document_id: str,
    body: dict = Body(default={}),
    user: UserContext = Depends(require_user),
) -> ShareQueuedResponse:
    """Share a personal document with the household (LUM-157).

    Owner-only, ``scope='personal'`` source (validated synchronously → 404 on a
    foreign/non-personal document; 400 on an invalid scope), then enqueues the
    background ``share_document`` projection job (→ 202 + job_id).
    """
    doc_id = _parse_document_id(document_id)
    scope = (body or {}).get("scope", "shared")
    if scope != "shared":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "invalid_scope"},
        )
    try:
        return doc_svc.share_document(user.user_id, doc_id)
    except DocumentNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "document_not_found"},
        ) from exc


@router.delete(
    "/{document_id}/publish",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=ShareQueuedResponse,
)
def unpublish_document(
    document_id: str,
    user: UserContext = Depends(require_user),
) -> ShareQueuedResponse:
    """Stop sharing a document with the household (LUM-157)."""
    doc_id = _parse_document_id(document_id)
    try:
        return doc_svc.unshare_document(user.user_id, doc_id)
    except (DocumentNotFoundError, DocumentNotSharedError) as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "document_not_found"},
        ) from exc
