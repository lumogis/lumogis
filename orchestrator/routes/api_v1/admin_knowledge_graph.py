# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Admin knowledge graph: status (LUM-937) and Turn on / Turn off (LUM-989).

``GET`` is read-only and admin-only: the configured and effective ``GRAPH_MODE``,
whether Core can reach the ``lumogis-graph`` service, whether this machine has
enough memory, one server-computed ``state`` and which buttons are available.
Mode tokens, booleans, coarse codes and byte counts only — never URLs, secrets or
env values.

``POST /enable`` and ``POST /disable`` (admin + same-origin) write ``.env``, record
the operation, answer ``202`` and then ask the stack-control sidecar to start or
stop the containers in the background (the sequence ends by recreating Core).
"""

from __future__ import annotations

from auth import UserContext
from authz import require_admin
from csrf import require_same_origin
from fastapi import APIRouter
from fastapi import BackgroundTasks
from fastapi import Depends
from fastapi import status
from fastapi.responses import JSONResponse
from models.api_v1 import AdminKnowledgeGraphStatus
from models.api_v1 import AdminKnowledgeGraphToggleRequest
from models.api_v1 import AdminKnowledgeGraphToggleResponse

from services import graph_status
from services import graph_toggle

router = APIRouter(
    prefix="/api/v1/admin/knowledge-graph",
    tags=["v1-admin-knowledge-graph"],
    dependencies=[Depends(require_admin)],
)


@router.get("", response_model=AdminKnowledgeGraphStatus)
def get_knowledge_graph_status() -> AdminKnowledgeGraphStatus:
    return graph_status.build_graph_status()


def _refuse(exc: graph_toggle.ToggleError) -> JSONResponse:
    """The ``409`` for a refusal; the body never carries more than a code."""
    if isinstance(exc, graph_toggle.NeedsConfirmation):
        body: dict = {"detail": "needs_confirmation"}
    elif isinstance(exc, graph_toggle.Blocked):
        body = {"detail": "blocked", "block": exc.code}
    else:
        body = {"detail": "busy"}
    return JSONResponse(status_code=status.HTTP_409_CONFLICT, content=body)


_TOGGLE_RESPONSES: dict = {
    409: {"description": "needs_confirmation, blocked (with ``block``) or busy."},
}


@router.post(
    "/enable",
    response_model=AdminKnowledgeGraphToggleResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses=_TOGGLE_RESPONSES,
    dependencies=[Depends(require_same_origin)],
)
def enable_knowledge_graph(
    body: AdminKnowledgeGraphToggleRequest,
    background: BackgroundTasks,
    user: UserContext = Depends(require_admin),
) -> AdminKnowledgeGraphToggleResponse | JSONResponse:
    try:
        graph_toggle.enable(confirm_low_memory=body.confirm_low_memory, actor=user.user_id)
    except graph_toggle.ToggleError as exc:
        return _refuse(exc)
    background.add_task(graph_toggle.run, "start")
    return AdminKnowledgeGraphToggleResponse(state="starting")


@router.post(
    "/disable",
    response_model=AdminKnowledgeGraphToggleResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses=_TOGGLE_RESPONSES,
    dependencies=[Depends(require_same_origin)],
)
def disable_knowledge_graph(
    background: BackgroundTasks,
    user: UserContext = Depends(require_admin),
) -> AdminKnowledgeGraphToggleResponse | JSONResponse:
    try:
        graph_toggle.disable(actor=user.user_id)
    except graph_toggle.ToggleError as exc:
        return _refuse(exc)
    background.add_task(graph_toggle.run, "stop")
    return AdminKnowledgeGraphToggleResponse(state="stopping")
