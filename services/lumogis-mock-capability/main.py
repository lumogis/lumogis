# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Non-product sample capability for Phase 5 compose / contract smoke only.

Contract fixture: ``GET /capabilities``, ``GET /health``, ``POST /tools/mock.echo_ping``.
No database; shared secret via ``MOCK_CAPABILITY_SHARED_SECRET`` for tool POSTs.
"""

from __future__ import annotations

import os
from typing import Any

from fastapi import FastAPI
from fastapi import Header
from fastapi import HTTPException
from fastapi import Request

SERVICE_ID = "lumogis.mock.echo"
MANIFEST: dict[str, Any] = {
    "name": "Lumogis mock echo capability",
    "id": SERVICE_ID,
    "version": "0.0.1",
    "type": "service",
    "transport": "http",
    "license_mode": "community",
    "maturity": "preview",
    "description": "Dev-only echo tool for second-capability compose smoke (not a product).",
    "tools": [
        {
            "name": "mock.echo_ping",
            "description": "Echo JSON body (requires bearer).",
            "license_mode": "community",
            "input_schema": {"type": "object"},
            "output_schema": {"type": "object"},
        }
    ],
    "health_endpoint": "/health",
    "capabilities_endpoint": "/capabilities",
    "permissions_required": [],
    "config_schema": {"type": "object"},
    "min_core_version": "0.3.0rc1",
    "maintainer": "lumogis-dev",
}

app = FastAPI(title="lumogis-mock-capability", version="0.0.1")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/capabilities")
def capabilities() -> dict[str, Any]:
    return MANIFEST


@app.post("/tools/mock.echo_ping")
async def mock_echo_ping(
    request: Request,
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> dict[str, Any]:
    secret = (os.environ.get("MOCK_CAPABILITY_SHARED_SECRET") or "").strip()
    if not secret:
        raise HTTPException(status_code=500, detail="MOCK_CAPABILITY_SHARED_SECRET unset")
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Bearer token required")
    token = authorization.removeprefix("Bearer ").strip()
    if token != secret:
        raise HTTPException(status_code=403, detail="invalid bearer")
    try:
        body = await request.json()
    except Exception:
        body = {}
    return {"ok": True, "echo": body}
