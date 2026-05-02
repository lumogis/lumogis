# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""KG service ASGI entrypoint.

Wires the FastAPI app, lifespan, scheduler, FastMCP mount, and
auth middleware. Start with:

    uvicorn main:app --host 0.0.0.0 --port 8001

(see `Dockerfile` for the production invocation; the directory name
contains a hyphen so the entrypoint is WORKDIR-relative, NOT the
dotted `services.lumogis-graph.main:app` path that FastAPI shorthand
docs assume.)

Lifespan behaviour:

  - STARTUP
      0. Load .env if present.
      1. Hard-fail if `GRAPH_BACKEND` is not `falkordb` (the service exists
         for FalkorDB; running it without one is misconfiguration that
         silently disables every code path here — better to fail fast).
      2. Initialise the in-process webhook executor.
      3. Construct the shared APScheduler. Register the daily reconcile +
         weekly quality jobs only when `KG_SCHEDULER_ENABLED=true` AND
         `GRAPH_MODE != "inprocess"` (defence in depth — see plan §B).
      4. Build a fresh FastMCP and swap it into the existing /mcp mount,
         then enter `session_manager.run()` so the StreamableHTTPSessionManager
         has its anyio task group running.
      5. Log the resolved `KG_MANAGEMENT_URL` and warn if it looks like
         the in-network default while the deployment hints (proxy /
         public hostname / external base URL) suggest a public exposure.

  - SHUTDOWN
      Mirrors startup in reverse:
        - Exit the MCP run context manager.
        - Shut down the scheduler.
        - Drain the webhook queue (so 202'd projections complete).
        - Close adapter connections.
"""

from __future__ import annotations

import logging
import os
import sys
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI

import config
import webhook_queue
from auth import auth_middleware
from __version__ import __version__

# TODO(structured_audit_logging): port orchestrator/logging_config.py
# to this service once a shared package exists. The structured-logging
# chunk landed structlog-based JSON/console bootstrap + request
# correlation + redaction in the orchestrator; replicating it here was
# explicitly out of scope (plan D2 — separate sys.path + separate
# requirements.txt makes byte-for-byte reuse impossible without a
# shared-package refactor first). See docs/structured-logging.md.
logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
_log = logging.getLogger("lumogis_graph.main")


# ---------------------------------------------------------------------------
# Routers + MCP module — imported eagerly so import errors surface during
# `uvicorn main:app` startup rather than on first request.
# ---------------------------------------------------------------------------

from routes.capabilities import router as capabilities_router  # noqa: E402
from routes.context import router as context_router            # noqa: E402
from routes.graph_admin_routes import router as graph_admin_router  # noqa: E402
from routes.health import router as health_router              # noqa: E402
from routes.mgm import router as mgm_router                    # noqa: E402
from routes.tools import router as tools_router                # noqa: E402
from routes.webhook import router as webhook_router            # noqa: E402

import graph as graph_pkg  # exposes `router` (backfill + viz)  # noqa: E402

try:
    from kg_mcp import server as mcp_server
except Exception:
    mcp_server = None  # type: ignore[assignment]
    _log.warning("kg_mcp.server not importable — KG /mcp surface disabled", exc_info=True)


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------


def _warn_if_management_url_default() -> None:
    """One-shot startup warning if KG_MANAGEMENT_URL looks unset for prod."""
    resolved = os.environ.get(
        "KG_MANAGEMENT_URL", "http://lumogis-graph:8001/mgm"
    ).strip()
    looks_default = resolved.startswith("http://lumogis-graph:")

    proxy_hint = os.environ.get("LUMOGIS_BEHIND_PROXY", "false").strip().lower() == "true"
    public_host = bool(os.environ.get("LUMOGIS_PUBLIC_HOSTNAME", "").strip())
    ext_url = os.environ.get("EXTERNAL_BASE_URL", "").strip()
    has_external_hint = bool(ext_url) and ("localhost" not in ext_url and "127.0.0.1" not in ext_url)

    if looks_default and (proxy_hint or public_host or has_external_hint):
        _log.warning(
            "KG_MANAGEMENT_URL appears to be the in-network default (%r) but the "
            "deployment hints suggest a public exposure "
            "(LUMOGIS_BEHIND_PROXY=%s, LUMOGIS_PUBLIC_HOSTNAME=%r, EXTERNAL_BASE_URL=%r). "
            "Operators MUST override KG_MANAGEMENT_URL to the externally-resolvable URL "
            "or external clients (Core's status page, MCP marketplaces) will receive "
            "an unreachable management URL in this service's CapabilityManifest.",
            resolved,
            os.environ.get("LUMOGIS_BEHIND_PROXY", ""),
            os.environ.get("LUMOGIS_PUBLIC_HOSTNAME", ""),
            ext_url,
        )


def _warn_if_insecure_webhooks() -> None:
    """Loud warning when the operator opted in to running without a webhook secret."""
    if config.kg_allow_insecure_webhooks() and config.get_kg_webhook_secret() is None:
        _log.warning(
            "KG_ALLOW_INSECURE_WEBHOOKS=true and GRAPH_WEBHOOK_SECRET is unset — "
            "/webhook and /context will accept ANY caller. NEVER USE IN PRODUCTION."
        )


def _hard_fail_if_no_falkordb() -> None:
    """Per the plan §"Error handling contract": exit 1 if the service can't do its job."""
    backend = os.environ.get("GRAPH_BACKEND", "falkordb").strip().lower()
    if backend != "falkordb":
        _log.error(
            "lumogis-graph requires GRAPH_BACKEND=falkordb (got %r). The KG service "
            "exists to drive FalkorDB; running it against a different backend is "
            "misconfiguration. Exiting.",
            backend,
        )
        sys.exit(1)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI lifespan: bring up scheduler/MCP/webhook pool, drain on shutdown."""
    load_dotenv(override=False)

    _hard_fail_if_no_falkordb()
    _warn_if_management_url_default()
    _warn_if_insecure_webhooks()

    _log.info("lumogis-graph %s starting (port=%s)", __version__, os.environ.get("KG_SERVICE_PORT", "8001"))
    _log.info(
        "graph store backend=%s url=%s",
        os.environ.get("GRAPH_BACKEND", "falkordb"),
        os.environ.get("FALKORDB_URL", "redis://falkordb:6379"),
    )

    workers = int(os.environ.get("KG_WEBHOOK_WORKERS", "4"))
    webhook_queue.init(workers=workers)

    scheduler = config.get_scheduler()
    graph_pkg.register_scheduled_jobs(scheduler)
    try:
        scheduler.start()
        _log.info("APScheduler started")
    except Exception:
        _log.exception("APScheduler failed to start — daily/weekly jobs will NOT run")

    app.state.mcp_run_cm = None
    if mcp_server is not None and mcp_server.mcp is not None and _mcp_mount_route is not None:
        try:
            fresh = mcp_server.build_fastmcp()
            mcp_server.mcp = fresh
            _mcp_mount_route.app = fresh.streamable_http_app()
            cm = fresh.session_manager.run()
            await cm.__aenter__()
            app.state.mcp_run_cm = cm
            _log.info("MCP session manager started (lumogis-graph /mcp)")
        except Exception:
            _log.exception("MCP session manager failed to start — /mcp will not respond")
            app.state.mcp_run_cm = None

    _log.info("lumogis-graph startup complete")
    yield

    if getattr(app.state, "mcp_run_cm", None) is not None:
        try:
            await app.state.mcp_run_cm.__aexit__(None, None, None)
            _log.info("MCP session manager stopped")
        except Exception:
            _log.exception("MCP session manager shutdown error")

    try:
        scheduler.shutdown(wait=False)
        _log.info("APScheduler shutdown")
    except Exception:
        _log.exception("APScheduler shutdown raised")

    webhook_queue.shutdown(wait=True)
    config.shutdown()
    _log.info("lumogis-graph shutdown complete")


# ---------------------------------------------------------------------------
# App, middleware, routes
# ---------------------------------------------------------------------------


app = FastAPI(
    title="Lumogis Graph Pro",
    description=(
        "Out-of-process knowledge-graph capability service. Exposes /webhook, "
        "/context, /mgm, /capabilities, /health, the operator-admin /kg/* + "
        "/graph/* endpoints, and a /mcp FastMCP surface."
    ),
    version=__version__,
    lifespan=lifespan,
)
app.middleware("http")(auth_middleware)
app.include_router(health_router)
app.include_router(capabilities_router)
app.include_router(webhook_router)
app.include_router(context_router)
app.include_router(mgm_router)
app.include_router(tools_router)
app.include_router(graph_admin_router)
app.include_router(graph_pkg.router)


# ---------------------------------------------------------------------------
# Mount the MCP server at /mcp when the SDK is installed.
#
# Same single-shot rebuild-on-lifespan pattern Core uses (see
# `orchestrator/main.py:299..328`). We hold onto the Mount route object
# at module-load so the lifespan can mutate `route.app` on each startup.
# ---------------------------------------------------------------------------

_mcp_mount_route = None
if mcp_server is not None and mcp_server.mcp is not None:
    try:
        app.mount("/mcp", mcp_server.mcp.streamable_http_app())
        from starlette.routing import Mount as _Mount

        for _r in reversed(app.routes):
            if isinstance(_r, _Mount) and _r.path == "/mcp":
                _mcp_mount_route = _r
                break
        _log.info("MCP server mounted at /mcp (stateless HTTP, JSON responses)")
    except Exception:
        _log.warning("Failed to mount MCP server at /mcp", exc_info=True)
