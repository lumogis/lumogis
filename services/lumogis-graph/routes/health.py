# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""KG service `/health` endpoint.

Contract (matches the plan §"KG service routes (new)"):

    GET /health
    -> 200 always
    -> {"status": "ok",
        "version": str,
        "falkordb": bool,
        "postgres": bool,
        "pending_webhook_tasks": int}

`pending_webhook_tasks` is the canonical contract surface used by:

  - The Docker HEALTHCHECK in `Dockerfile`.
  - Core's `services/capability_registry.py` health probe.
  - `tests/integration/wait_for_idle.py` (the parity test idle gate).

It MUST come from `webhook_queue.qsize()` (the public `_inflight` gauge),
NOT from `executor._work_queue.qsize()` which is private CPython API.

The endpoint returns 200 even when FalkorDB or Postgres are unreachable;
operators read the boolean fields to distinguish "service alive but
backends degraded" from "service down" (which manifests as a connection
refused at the TCP layer instead of a 5xx).
"""

import logging

from fastapi import APIRouter

import config
import webhook_queue
from __version__ import __version__

router = APIRouter()
_log = logging.getLogger(__name__)


def _check_falkordb() -> bool:
    try:
        gs = config.get_graph_store()
        if gs is None:
            return False
        if hasattr(gs, "ping"):
            return bool(gs.ping())
        gs.query("RETURN 1", {})
        return True
    except Exception:
        return False


def _check_postgres() -> bool:
    try:
        ms = config.get_metadata_store()
        if hasattr(ms, "ping"):
            return bool(ms.ping())
        ms.fetch_one("SELECT 1 AS ok")
        return True
    except Exception:
        return False


@router.get("/health")
def health() -> dict:
    """Always-200 liveness probe with backend booleans + queue depth."""
    return {
        "status": "ok",
        "version": __version__,
        "falkordb": _check_falkordb(),
        "postgres": _check_postgres(),
        "pending_webhook_tasks": webhook_queue.qsize(),
    }
