# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""KG service code-structure ingest route (LUM-301).

    POST /codegraph/reindex
      body: {"roots"?: [str], "bank"?: str}  (defaults from env)
      -> 200 IngestReport
      -> 403 admin token required
      -> 409 codegraph not enabled (LUMOGIS_CODEGRAPH_ENABLED=false)
      -> 400 requested root outside the configured allowlist
      -> 503 graph store / FalkorDB unavailable

Admin-gated (same `X-Graph-Admin-Token` posture as `graph_admin_routes`). Runs
the walk+parse+project in a worker thread and returns the report synchronously
(no streaming in v1).
"""

import hmac
import logging
import os

from codegraph.models import ReindexRequest
from fastapi import APIRouter
from fastapi import HTTPException
from fastapi import Request
from fastapi.concurrency import run_in_threadpool

import config

router = APIRouter()
_log = logging.getLogger(__name__)


def _require_admin(request: Request) -> None:
    admin_token = os.environ.get("GRAPH_ADMIN_TOKEN", "")
    if not admin_token:
        # P3-2: when no token is configured the route is open — the same
        # dev-default posture as graph_admin_routes._require_admin. Operators set
        # GRAPH_ADMIN_TOKEN to enforce the documented 403.
        return
    presented = request.headers.get("X-Graph-Admin-Token", "")
    # Bytes, not str: see routes/webhook.py::check_webhook_auth.
    if not presented or not hmac.compare_digest(presented.encode(), admin_token.encode()):
        raise HTTPException(
            status_code=403,
            detail="Admin token required (X-Graph-Admin-Token header)",
        )


def _validate_roots(requested: list[str]) -> list[str]:
    """Each requested root must resolve within the configured allowlist."""
    allow = [os.path.realpath(r) for r in config.get_codegraph_roots()]
    out: list[str] = []
    for r in requested:
        real = os.path.realpath(r)
        if not any(real == a or real.startswith(a + os.sep) for a in allow):
            raise HTTPException(
                status_code=400,
                detail="root outside the configured LUMOGIS_CODEGRAPH_ROOTS allowlist",
            )
        out.append(real)
    return out


@router.post("/codegraph/reindex")
async def reindex(request: Request, body: ReindexRequest) -> dict:
    _require_admin(request)

    if not config.get_codegraph_enabled():
        raise HTTPException(
            status_code=409,
            detail="codegraph not enabled (set LUMOGIS_CODEGRAPH_ENABLED=true)",
        )

    if config.get_graph_store() is None:
        raise HTTPException(status_code=503, detail="graph store unavailable")

    roots = _validate_roots(body.roots) if body.roots else None

    # Imported lazily so the route module (and the app) load without tree-sitter
    # present when codegraph is disabled.
    from codegraph.ingest import ingest_roots

    try:
        report = await run_in_threadpool(ingest_roots, roots, body.bank)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return report.as_dict()
