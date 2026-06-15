# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Typed `/api/v1/admin/ollama/*` admin routes (LUM-451)."""

from __future__ import annotations

from authz import require_admin
from fastapi import APIRouter
from fastapi import BackgroundTasks
from fastapi import Depends
from fastapi import HTTPException
from fastapi import Request
from fastapi.responses import JSONResponse
from models.api_v1 import OllamaDeleteResponse
from models.api_v1 import OllamaDiscoveryResponse
from models.api_v1 import OllamaModelNameRequest
from models.api_v1 import OllamaPullActiveResponse
from models.api_v1 import OllamaPullJob
from models.api_v1 import OllamaPullStartResponse

from services import admin_ollama as admin_ollama_svc
from services.ollama_pull_jobs import JobAlreadyRunning
from services.ollama_pull_jobs import create_job
from services.ollama_pull_jobs import get_active_job
from services.ollama_pull_jobs import get_job
from services.ollama_pull_jobs import job_to_response
from services.ollama_pull_jobs import run_pull_job

router = APIRouter(
    prefix="/api/v1/admin/ollama",
    tags=["admin-ollama"],
    dependencies=[Depends(require_admin)],
)


@router.get(
    "/discovery",
    response_model=OllamaDiscoveryResponse,
    response_model_exclude_none=True,
)
def ollama_discovery_v1() -> OllamaDiscoveryResponse:
    """Return local Ollama models and the public catalog (typed v1)."""
    return OllamaDiscoveryResponse.model_validate(admin_ollama_svc.build_ollama_discovery())


@router.post(
    "/pull/async",
    status_code=202,
    response_model=OllamaPullStartResponse,
    responses={202: {"model": OllamaPullStartResponse}},
)
def ollama_pull_async_v1(
    request: Request,
    body: OllamaModelNameRequest,
    background_tasks: BackgroundTasks,
):
    """Start an async Ollama pull; poll job status for progress."""
    name = admin_ollama_svc.validate_model_name(body.name)
    try:
        job_id = create_job(name)
    except JobAlreadyRunning:
        raise HTTPException(status_code=409, detail="ollama pull already in progress") from None
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Could not create pull job: {exc}") from exc

    background_tasks.add_task(run_pull_job, job_id, request.app.state)
    return JSONResponse(
        status_code=202,
        content=OllamaPullStartResponse(status="started", job_id=job_id).model_dump(),
    )


@router.get("/pull/jobs/active", response_model=OllamaPullActiveResponse)
def ollama_pull_job_active_v1() -> OllamaPullActiveResponse:
    """Return the latest pending or running pull job, if any."""
    row = get_active_job()
    if row is None:
        return OllamaPullActiveResponse(job=None)
    return OllamaPullActiveResponse(job=OllamaPullJob.model_validate(job_to_response(row)))


@router.get("/pull/jobs/{job_id}", response_model=OllamaPullJob)
def ollama_pull_job_get_v1(job_id: str) -> OllamaPullJob:
    """Poll async Ollama pull job status."""
    row = get_job(job_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Unknown pull job.")
    return OllamaPullJob.model_validate(job_to_response(row))


@router.post("/delete", response_model=OllamaDeleteResponse)
def ollama_delete_v1(body: OllamaModelNameRequest) -> OllamaDeleteResponse:
    """Remove a locally pulled Ollama model."""
    payload = admin_ollama_svc.delete_model(body.name)
    return OllamaDeleteResponse.model_validate(payload)
