# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Public capability discovery endpoint.

Exposes GET /capabilities returning Lumogis Core's own CapabilityManifest
(see orchestrator/models/capability.py). External systems — Thunderbolt,
MCP clients, future capability marketplaces — discover Core through the
same contract that Area 1 defined for out-of-process capability services.

Design note
-----------
Core never registers itself in its own CapabilityRegistry. The registry is
strictly for *out-of-process* services declared via CAPABILITY_SERVICE_URLS.
This endpoint exists purely for symmetric external discovery.
"""

import logging

import mcp_server
from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter()
_log = logging.getLogger(__name__)


@router.get("/capabilities")
def capabilities() -> JSONResponse:
    """Return the CapabilityManifest describing Lumogis Core itself."""
    manifest = mcp_server.build_core_manifest()
    # model_dump(mode="json") serialises enums to their string values, which
    # is what external manifest consumers expect.
    return JSONResponse(content=manifest.model_dump(mode="json"))
