# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Publish / unpublish endpoints for the personal/shared/system scope model.

Implements the 12 v1 routes defined by plan §2.5 / §2.14:

    POST   /api/v1/notes/{id}/publish        body {"scope": "shared"}
    DELETE /api/v1/notes/{id}/publish
    POST   /api/v1/audio_memos/{id}/publish
    DELETE /api/v1/audio_memos/{id}/publish
    POST   /api/v1/sessions/{id}/publish
    DELETE /api/v1/sessions/{id}/publish
    POST   /api/v1/files/{id}/publish        (id is INTEGER for file_index)
    DELETE /api/v1/files/{id}/publish
    POST   /api/v1/entities/{id}/publish
    DELETE /api/v1/entities/{id}/publish
    POST   /api/v1/signals/{id}/publish
    DELETE /api/v1/signals/{id}/publish

All write semantics live in :mod:`services.projection`; this module is
responsible only for HTTP shape (auth, body validation, source-row
load, error mapping). Implementation is generic: every route delegates
to ``_publish_one`` / ``_unpublish_one`` and dispatches via the
``_RESOURCE_REGISTRY`` table.

v1 contract pins (plan §2.5):

* ``scope`` in the request body is restricted to ``"shared"``. A
  request with ``"scope": "system"`` returns HTTP 400. System-scoped
  rows are produced exclusively by system-owned writers.
* The ``id`` path segment must reference a row owned by the caller
  (``user_id = caller``, ``scope = 'personal'``). Any other case
  returns HTTP 404 — we never reveal whether the row exists for
  another user.
* Publishing a staged entity returns HTTP 409 (plan §23) so the
  household never sees a quarantined row.
* Idempotency is guaranteed by ``services.projection`` via
  deterministic uuid5 PKs + the partial unique index
  ``<table>_published_from_scope_uniq``; a duplicate POST returns
  the same projection row.

See also: ``.cursor/plans/personal_shared_system_memory_scopes.plan.md``
``orchestrator/services/projection.py``
"""

from __future__ import annotations

import logging
from typing import Any
from typing import Callable
from typing import Optional

from auth import UserContext
from auth import get_user
from fastapi import APIRouter
from fastapi import Body
from fastapi import Depends
from fastapi import HTTPException
from fastapi import Response

import config
from services import projection as proj

_log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["scope"])


# ---------------------------------------------------------------------------
# Resource registry
# ---------------------------------------------------------------------------


ResourceFetcher = Callable[[str, str], Optional[dict]]
ResourceProjector = Callable[..., dict]
ResourceUnprojector = Callable[..., int]


def _fetch_uuid_pk(table: str, pk_col: str) -> ResourceFetcher:
    """Return a fetcher that loads a personal-scoped row by UUID PK + owner."""

    def _fetch(pk: str, user_id: str) -> Optional[dict]:
        ms = config.get_metadata_store()
        return ms.fetch_one(
            f"SELECT * FROM {table} WHERE {pk_col} = %s AND user_id = %s AND scope = 'personal'",
            (pk, user_id),
        )

    return _fetch


def _fetch_int_pk(table: str, pk_col: str) -> ResourceFetcher:
    """Return a fetcher for INTEGER-PK resources (file_index)."""

    def _fetch(pk: str, user_id: str) -> Optional[dict]:
        try:
            pk_int = int(pk)
        except (TypeError, ValueError):
            return None
        ms = config.get_metadata_store()
        return ms.fetch_one(
            f"SELECT * FROM {table} WHERE {pk_col} = %s AND user_id = %s AND scope = 'personal'",
            (pk_int, user_id),
        )

    return _fetch


def _entity_publish_guard(src: dict) -> None:
    """Refuse publish for staged entities (plan §23)."""
    if src.get("is_staged"):
        raise HTTPException(
            status_code=409,
            detail={
                "error": "entity_is_staged",
                "message": (
                    "Entity is quarantined; promote it via "
                    "/admin/review-queue/decide before publishing."
                ),
            },
        )


# Per-resource registration. Each entry maps the public route name to a
# concrete fetcher + projector + unprojector trio. Keeping every
# resource on the same shape lets the 12 routes share one `_publish_one`
# / `_unpublish_one` implementation.
_RESOURCE_REGISTRY: dict[str, dict[str, Any]] = {
    "notes": {
        "table": "notes",
        "pk_col": "note_id",
        "pk_type": "uuid",
        "fetch": _fetch_uuid_pk("notes", "note_id"),
        "project": proj.project_note,
        "unproject": proj.unproject_note,
        "pre_publish": None,
    },
    "audio_memos": {
        "table": "audio_memos",
        "pk_col": "audio_id",
        "pk_type": "uuid",
        "fetch": _fetch_uuid_pk("audio_memos", "audio_id"),
        "project": proj.project_audio_memo,
        "unproject": proj.unproject_audio_memo,
        "pre_publish": None,
    },
    "sessions": {
        "table": "sessions",
        "pk_col": "session_id",
        "pk_type": "uuid",
        "fetch": _fetch_uuid_pk("sessions", "session_id"),
        "project": proj.project_session,
        "unproject": proj.unproject_session,
        "pre_publish": None,
    },
    # Public route segment is /files/ but the underlying table is file_index.
    "files": {
        "table": "file_index",
        "pk_col": "id",
        "pk_type": "int",
        "fetch": _fetch_int_pk("file_index", "id"),
        "project": proj.project_file,
        "unproject": proj.unproject_file,
        "pre_publish": None,
    },
    "entities": {
        "table": "entities",
        "pk_col": "entity_id",
        "pk_type": "uuid",
        "fetch": _fetch_uuid_pk("entities", "entity_id"),
        "project": proj.project_entity,
        "unproject": proj.unproject_entity,
        "pre_publish": _entity_publish_guard,
    },
    "signals": {
        "table": "signals",
        "pk_col": "signal_id",
        "pk_type": "uuid",
        "fetch": _fetch_uuid_pk("signals", "signal_id"),
        "project": proj.project_signal,
        "unproject": proj.unproject_signal,
        "pre_publish": None,
    },
}


# ---------------------------------------------------------------------------
# Generic publish / unpublish helpers
# ---------------------------------------------------------------------------


def _validate_publish_scope(body: Optional[dict]) -> str:
    """Enforce v1 contract: only ``scope='shared'`` is accepted."""
    target = (body or {}).get("scope", "shared")
    if target != "shared":
        raise HTTPException(
            status_code=400,
            detail={
                "error": "invalid_scope",
                "message": (
                    "Only scope='shared' is accepted by v1 publish routes. "
                    "system-scoped rows are produced by system-owned writers."
                ),
            },
        )
    return target


def _publish_one(
    *,
    resource: str,
    pk: str,
    body: Optional[dict],
    actor: UserContext,
) -> dict:
    cfg = _RESOURCE_REGISTRY[resource]
    target_scope = _validate_publish_scope(body)

    src = cfg["fetch"](pk, actor.user_id)
    if src is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "not_found",
                "message": f"{resource}/{pk} not found or not owned by caller",
            },
        )

    pre = cfg["pre_publish"]
    if pre is not None:
        pre(src)

    try:
        row = cfg["project"](src, target_scope=target_scope, actor=actor)
    except Exception as exc:
        _log.exception(
            "publish %s/%s failed scope=%s actor=%s",
            resource,
            pk,
            target_scope,
            actor.user_id,
        )
        raise HTTPException(
            status_code=502,
            detail={
                "error": "projection_failed",
                "message": str(exc),
            },
        )

    return _serialise_projection(resource, row)


def _unpublish_one(
    *,
    resource: str,
    pk: str,
    actor: UserContext,
) -> Response:
    cfg = _RESOURCE_REGISTRY[resource]
    src = cfg["fetch"](pk, actor.user_id)
    if src is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "not_found",
                "message": f"{resource}/{pk} not found or not owned by caller",
            },
        )

    pk_for_unproject: Any = pk
    if cfg["pk_type"] == "int":
        try:
            pk_for_unproject = int(pk)
        except (TypeError, ValueError):
            raise HTTPException(status_code=404, detail={"error": "not_found"})

    try:
        cfg["unproject"](pk_for_unproject)
    except Exception as exc:
        _log.exception(
            "unpublish %s/%s failed actor=%s",
            resource,
            pk,
            actor.user_id,
        )
        raise HTTPException(
            status_code=502,
            detail={
                "error": "unprojection_failed",
                "message": str(exc),
            },
        )

    # 204 even when no row was deleted (idempotent unpublish).
    return Response(status_code=204)


def _serialise_projection(resource: str, row: dict) -> dict:
    """Strip internal columns before returning to the user.

    ``published_from`` is intentionally elided per plan §19 — it is
    provenance metadata exposed only by admin-only routes.
    """
    cfg = _RESOURCE_REGISTRY[resource]
    pk_col = cfg["pk_col"]
    out: dict[str, Any] = {
        "resource": resource,
        "scope": row.get("scope") or "shared",
    }
    if pk_col in row:
        out[pk_col] = row[pk_col]
    # Echo a small set of human-meaningful fields so clients can confirm
    # the projection without a follow-up GET. Anything more belongs in
    # the resource's GET endpoint.
    for field in ("name", "title", "summary", "text", "transcript", "url"):
        if row.get(field):
            out[field] = row[field]
    return out


# ---------------------------------------------------------------------------
# Notes
# ---------------------------------------------------------------------------


@router.post("/notes/{note_id}/publish")
def publish_note(
    note_id: str,
    body: dict = Body(default={}),
    user: UserContext = Depends(get_user),
):
    return _publish_one(resource="notes", pk=note_id, body=body, actor=user)


@router.delete("/notes/{note_id}/publish")
def unpublish_note(
    note_id: str,
    user: UserContext = Depends(get_user),
):
    return _unpublish_one(resource="notes", pk=note_id, actor=user)


# ---------------------------------------------------------------------------
# Audio memos
# ---------------------------------------------------------------------------


@router.post("/audio_memos/{audio_id}/publish")
def publish_audio_memo(
    audio_id: str,
    body: dict = Body(default={}),
    user: UserContext = Depends(get_user),
):
    return _publish_one(resource="audio_memos", pk=audio_id, body=body, actor=user)


@router.delete("/audio_memos/{audio_id}/publish")
def unpublish_audio_memo(
    audio_id: str,
    user: UserContext = Depends(get_user),
):
    return _unpublish_one(resource="audio_memos", pk=audio_id, actor=user)


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------


@router.post("/sessions/{session_id}/publish")
def publish_session(
    session_id: str,
    body: dict = Body(default={}),
    user: UserContext = Depends(get_user),
):
    return _publish_one(resource="sessions", pk=session_id, body=body, actor=user)


@router.delete("/sessions/{session_id}/publish")
def unpublish_session(
    session_id: str,
    user: UserContext = Depends(get_user),
):
    return _unpublish_one(resource="sessions", pk=session_id, actor=user)


# ---------------------------------------------------------------------------
# Files (file_index — INTEGER PK)
# ---------------------------------------------------------------------------


@router.post("/files/{file_id}/publish")
def publish_file(
    file_id: str,
    body: dict = Body(default={}),
    user: UserContext = Depends(get_user),
):
    return _publish_one(resource="files", pk=file_id, body=body, actor=user)


@router.delete("/files/{file_id}/publish")
def unpublish_file(
    file_id: str,
    user: UserContext = Depends(get_user),
):
    return _unpublish_one(resource="files", pk=file_id, actor=user)


# ---------------------------------------------------------------------------
# Entities
# ---------------------------------------------------------------------------


@router.post("/entities/{entity_id}/publish")
def publish_entity(
    entity_id: str,
    body: dict = Body(default={}),
    user: UserContext = Depends(get_user),
):
    return _publish_one(resource="entities", pk=entity_id, body=body, actor=user)


@router.delete("/entities/{entity_id}/publish")
def unpublish_entity(
    entity_id: str,
    user: UserContext = Depends(get_user),
):
    return _unpublish_one(resource="entities", pk=entity_id, actor=user)


# ---------------------------------------------------------------------------
# Signals
# ---------------------------------------------------------------------------


@router.post("/signals/{signal_id}/publish")
def publish_signal(
    signal_id: str,
    body: dict = Body(default={}),
    user: UserContext = Depends(get_user),
):
    return _publish_one(resource="signals", pk=signal_id, body=body, actor=user)


@router.delete("/signals/{signal_id}/publish")
def unpublish_signal(
    signal_id: str,
    user: UserContext = Depends(get_user),
):
    return _unpublish_one(resource="signals", pk=signal_id, actor=user)


__all__ = ["router"]
