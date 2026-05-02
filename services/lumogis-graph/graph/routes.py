# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Graph plugin HTTP routes.

M2 adds:
  POST /graph/backfill  — admin-only; triggers a one-time stale-row reconciliation
                          in the background.  Returns 202 immediately.  Returns 409
                          if a backfill is already running.

M4 will add:
  GET  /graph/ego       — ego-network for a given entity
  GET  /graph/path      — shortest path between two entities
  GET  /graph/search    — search for entities by name
  GET  /graph/stats     — graph statistics

Admin-only enforcement
----------------------
POST /graph/backfill is a privileged operation — running it against a large
dataset can be expensive and should not be triggerable by any authenticated
user.  The pattern used here matches the RESTART_SECRET approach already in
the codebase (routes/admin.py):

  GRAPH_ADMIN_TOKEN env var + X-Graph-Admin-Token request header

When GRAPH_ADMIN_TOKEN is set:
  - Missing or wrong header → 403 Forbidden (even if authenticated)
  - Correct header → proceed (admin confirmed)

When GRAPH_ADMIN_TOKEN is not set (dev / default install):
  - Auth check falls through to AUTH_ENABLED / is_authenticated only
  - Consistent with the open-dev posture of other admin operations

Status code contracts:
  401 — AUTH_ENABLED=true and request is not authenticated
  403 — authenticated but GRAPH_ADMIN_TOKEN mismatch (not admin)
  503 — graph store not configured
  409 — backfill already running
  202 — accepted, running in background

In-process running guard
------------------------
A threading.Event is used as a simple "is running" flag.  This is intentional:
it matches the current app architecture (single process, no external job system).
If the process restarts mid-backfill, the flag resets cleanly.
"""

import logging
import os
import threading

from auth import get_user
from fastapi import APIRouter
from fastapi import BackgroundTasks
from fastapi import HTTPException
from fastapi import Request

_log = logging.getLogger(__name__)

router = APIRouter(prefix="/graph", tags=["graph"])

# Module-level guard: set while a backfill is running, cleared when done.
_backfill_running = threading.Event()


def _check_admin(request: Request) -> None:
    """Enforce admin access on the request.

    Step 1 — Authentication (when AUTH_ENABLED=true):
      Unauthenticated → 401

    Step 2 — Admin token (when GRAPH_ADMIN_TOKEN is set):
      Missing or wrong X-Graph-Admin-Token header → 403

    When neither guard is configured (dev default):
      Request is allowed — consistent with dashboard and other admin ops.
    """
    user = get_user(request)

    if os.environ.get("AUTH_ENABLED", "false").lower() == "true" and not user.is_authenticated:
        raise HTTPException(status_code=401, detail="Authentication required")

    admin_token = os.environ.get("GRAPH_ADMIN_TOKEN", "")
    if admin_token:
        supplied = request.headers.get("X-Graph-Admin-Token", "")
        if not supplied or supplied != admin_token:
            raise HTTPException(
                status_code=403,
                detail="Admin token required (X-Graph-Admin-Token header)",
            )


def _run_backfill(limit_per_type: int | None) -> None:
    """Execute reconciliation and clear the running flag when complete."""
    try:
        from graph.reconcile import run_reconciliation

        result = run_reconciliation(limit_per_type=limit_per_type)
        totals = result.get("totals", {})
        _log.info(
            "Graph backfill complete: scanned=%d projected_ok=%d "
            "projected_failed=%d stamped=%d duration_ms=%d",
            totals.get("scanned", 0),
            totals.get("projected_ok", 0),
            totals.get("projected_failed", 0),
            totals.get("stamped", 0),
            totals.get("duration_ms", 0),
        )
    except Exception:
        _log.exception("Graph backfill: unhandled error during reconciliation")
    finally:
        _backfill_running.clear()


@router.post("/backfill", status_code=202)
def trigger_backfill(
    bg: BackgroundTasks,
    request: Request,
    limit_per_type: int | None = None,
):
    """Trigger a one-time graph backfill (admin-only).

    Replays all stale Postgres rows into FalkorDB using the same projection
    logic as the scheduled reconciliation job.  Only processes rows where
    graph_projected_at IS NULL OR updated_at > graph_projected_at.

    Parameters:
        limit_per_type: optional cap on rows processed per table (query param).
                        Default: no limit (all stale rows).  Use a small value
                        for incremental catch-up on large datasets.

    Returns:
        202 Accepted             — backfill started in the background
        401 Unauthorized         — not authenticated (AUTH_ENABLED=true)
        403 Forbidden            — authenticated but not admin (GRAPH_ADMIN_TOKEN set)
        409 Conflict             — a backfill is already running
        503 Service Unavailable  — graph store not configured
    """
    import config

    _check_admin(request)

    # Verify graph store is configured before accepting the job
    gs = config.get_graph_store()
    if gs is None:
        raise HTTPException(
            status_code=503,
            detail="Graph store not configured (GRAPH_BACKEND is not 'falkordb')",
        )

    # Reject concurrent runs
    if _backfill_running.is_set():
        raise HTTPException(
            status_code=409,
            detail="A backfill is already running. Try again when it completes.",
        )

    _backfill_running.set()
    bg.add_task(_run_backfill, limit_per_type)

    user = get_user(request)
    _log.info(
        "Graph backfill: started by user_id=%s limit_per_type=%s",
        user.user_id,
        limit_per_type if limit_per_type is not None else "unlimited",
    )
    return {
        "status": "backfill_started",
        "limit_per_type": limit_per_type,
        "message": (
            "Stale graph projection units are being replayed in the background. "
            "Check application logs for progress."
        ),
    }
