# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Graph package for the standalone lumogis-graph service.

This is the KG-side counterpart to Core's `orchestrator/plugins/graph/__init__.py`,
rewritten for an out-of-process world. The big differences:

- **No Core hook bus.** The KG service does not import `hooks` or `events`
  from Core. The HTTP `/webhook` endpoint is the trigger; `routes/webhook.py`
  validates the envelope, picks the right `graph.writer.on_*` handler, and
  enqueues it via `webhook_queue.submit(handler, **payload_kwargs)`.
- **No `query_graph` ToolSpec.** Core's tool dispatcher is not running in
  this process. The six `graph.*` tools are exposed via the FastMCP server
  mounted at `/mcp` (see `mcp/server.py`) and via the proxy ToolSpec that
  Core registers when `GRAPH_MODE=service` (see `register_query_graph_proxy`
  in Core's `orchestrator/services/tools.py`).
- **Local APScheduler, not Core's.** `register_scheduled_jobs(scheduler)` is
  called from `main.py` lifespan and wires the daily reconciliation pass
  (incl. orphan-node GC) and the weekly quality job onto the KG service's
  own scheduler instance. Both are guarded by `KG_SCHEDULER_ENABLED` AND
  `GRAPH_MODE != "inprocess"` so a misconfigured shared `.env` cannot
  cause Core and KG to scan the same Postgres rows in parallel.

The router exposed at `router` aggregates the legacy backfill and viz
routers exactly as Core's plugin did, so `main.py` can `app.include_router(
graph.router)` and pick up `/graph/backfill` + `/graph/viz/*` without
restating each one.
"""

import logging
import os

import config

_log = logging.getLogger(__name__)


def _scheduler_should_run() -> bool:
    """Return True iff this process should own the daily/weekly graph jobs.

    Two guards (both must be true):
      1. `KG_SCHEDULER_ENABLED=true` (default `true`) — operator opt-out
         for split-control deployments where a sidecar runs scheduled work.
      2. `GRAPH_MODE != "inprocess"` — defence in depth: if Core is in
         inprocess mode AND a KG container is somehow also running against
         the same Postgres, both schedulers would scan the same stale rows
         and double-write `edge_scores` / `graph_projected_at`. Refusing
         to schedule when Core thinks it owns the graph is the safe default.
    """
    enabled = os.environ.get("KG_SCHEDULER_ENABLED", "true").strip().lower() != "false"
    mode = os.environ.get("GRAPH_MODE", "service").strip().lower()
    return enabled and mode != "inprocess"


def register_scheduled_jobs(scheduler) -> None:
    """Wire the daily reconciliation + weekly quality jobs onto `scheduler`.

    Called from `main.py` lifespan after the scheduler has been constructed
    but before it is started. No-op (with a single INFO log) when
    `_scheduler_should_run()` is False so operators see WHY no jobs ran.
    """
    if not _scheduler_should_run():
        _log.info(
            "graph: scheduler disabled (KG_SCHEDULER_ENABLED=%s, GRAPH_MODE=%s) "
            "— daily reconciliation and weekly quality NOT registered",
            os.environ.get("KG_SCHEDULER_ENABLED", "true"),
            os.environ.get("GRAPH_MODE", "service"),
        )
        return

    try:
        from graph.reconcile import run_reconciliation

        scheduler.add_job(
            run_reconciliation,
            trigger="cron",
            hour=3,
            minute=0,
            id="graph_reconciliation",
            replace_existing=True,
        )
        _log.info("graph: reconciliation job scheduled (daily 03:00, includes orphan-node GC)")
    except Exception:
        _log.exception("graph: failed to register reconciliation job")

    try:
        from quality.edge_quality import run_weekly_quality_job

        # Same trigger as Core's inprocess plugin (Sunday 04:00). Operators
        # can override via `WEEKLY_QUALITY_CRON` env var by replacing this
        # call with `scheduler.add_job(..., trigger=CronTrigger.from_crontab(env))`.
        scheduler.add_job(
            run_weekly_quality_job,
            trigger="cron",
            day_of_week="sun",
            hour=4,
            minute=0,
            id="graph_weekly_quality",
            replace_existing=True,
        )
        _log.info("graph: weekly quality job scheduled (Sun 04:00)")
    except ImportError:
        _log.warning("graph: quality.edge_quality.run_weekly_quality_job not found — weekly job NOT scheduled")
    except Exception:
        _log.exception("graph: failed to register weekly quality job")


# ---------------------------------------------------------------------------
# Combined router for /graph/backfill + /graph/viz/*
# ---------------------------------------------------------------------------
# Imported lazily in main.py via `from graph import router`. The two child
# routers below have no module-level side effects beyond defining
# `APIRouter()` instances, so importing them here is safe.

from fastapi import APIRouter as _APIRouter  # noqa: E402
from graph.routes import router as _backfill_router  # noqa: E402
from graph.viz_routes import router as _viz_router  # noqa: E402

router = _APIRouter()
router.include_router(_backfill_router)
router.include_router(_viz_router)


# Touch `config` to guarantee it is importable when this package is loaded
# from `main.py`. Failing here at import time is a much clearer signal than
# a NameError raised by the first webhook handler.
_ = config.__name__
