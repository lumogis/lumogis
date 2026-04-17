# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Lumogis
"""
Lumogis orchestrator – FastAPI app.

App creation, lifespan (health checks, collection init, shutdown),
and router includes. All endpoint logic lives in routes/.
"""

import logging
import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv

load_dotenv()

import hooks
from auth import auth_middleware
from fastapi import FastAPI
from plugins import load_plugins
from routes.actions import router as actions_router
from routes.admin import router as admin_router
from routes.chat import router as chat_router
from routes.data import router as data_router
from routes.events import register_hooks as register_sse_hooks
from routes.events import router as events_router
from routes.signals import router as signals_router

import config

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
_log = logging.getLogger(__name__)

# Qdrant collections created on startup.
# "signals" stores embedded content summaries for semantic dedup.
# Vector size 768 matches Nomic Embed. Changing later requires drop + re-index.
_COLLECTIONS = ["documents", "conversations", "entities", "signals"]


@asynccontextmanager
async def lifespan(app: FastAPI):
    _log.info("Startup: pinging backends...")

    # Qdrant and Postgres are hard requirements — crash if unavailable.
    for name, getter in [
        ("vector_store", config.get_vector_store),
        ("metadata_store", config.get_metadata_store),
    ]:
        backend = getter()
        if not backend.ping():
            raise RuntimeError(
                f"STARTUP FAILED: {name} ({type(backend).__name__}) is unreachable. "
                f"Check the service and connection settings in .env."
            )
        _log.info("  %s (%s): OK", name, type(backend).__name__)

    # Embedder is a soft requirement — start in degraded mode if model unavailable.
    # ping() checks both Ollama liveness AND model presence (via /api/show).
    # False means "Ollama is down" OR "model not yet pulled" — log covers both.
    embedder = config.get_embedder()
    _embedding_ready = False
    if not embedder.ping():
        _log.warning(
            "Embedding model not ready at startup (Ollama unreachable or %s not pulled). "
            "Search and ingest will be unavailable. "
            "Open http://localhost:8000/dashboard → Settings → Models to pull the model, "
            "or check that the Ollama service is running.",
            os.environ.get("EMBEDDING_MODEL", "nomic-embed-text"),
        )
    else:
        try:
            vs = config.get_vector_store()
            dim = embedder.vector_size
            for coll in _COLLECTIONS:
                vs.create_collection(coll, dim)
            _embedding_ready = True
            _log.info("Embedder vector size: %d — collections ready", dim)
        except Exception as exc:
            _log.warning(
                "Embedding model not ready (%s). "
                "Qdrant collections will be created when the model becomes available. "
                "Pull %s via http://localhost:8000/dashboard → Settings → Models.",
                exc,
                os.environ.get("EMBEDDING_MODEL", "nomic-embed-text"),
            )

    app.state.embedding_ready = _embedding_ready

    try:
        reranker = config.get_reranker()
    except Exception as exc:
        _log.warning("Reranker failed to load (%s) — search will proceed without reranking", exc)
        config._instances["reranker"] = None
        reranker = None

    if reranker is not None and _embedding_ready:
        try:
            reranker.warmup()
        except Exception as exc:
            _log.warning("Reranker warmup failed (%s) — search will proceed without reranking", exc)
            config._instances["reranker"] = None
    elif reranker is not None:
        _log.info("Reranker warmup skipped — embedding not ready")
    else:
        _log.info("Reranker disabled (RERANKER_BACKEND=none)")

    from permissions import seed_defaults

    seed_defaults()

    plugin_routers = load_plugins()
    for plugin_router in plugin_routers:
        app.include_router(plugin_router)

    from services.ingest import start_watcher

    start_watcher("/workspace/inbox")

    # APScheduler: start scheduler then load signal sources.
    scheduler = config.get_scheduler()
    scheduler.start()
    _log.info("APScheduler started")

    from signals import start_all as start_signal_monitors

    start_signal_monitors()

    # Register SSE hook callbacks and built-in routines.
    register_sse_hooks()

    from services.routines import register_all as register_routines

    register_routines()

    # Capability service registry: discover out-of-process services declared
    # in CAPABILITY_SERVICE_URLS, then immediately probe each one's health
    # endpoint so the very first GET / after startup reflects real state
    # rather than the default healthy=False placeholders. Both steps are
    # warnings-only — Core must start cleanly even when zero services are
    # reachable or when every service is down.
    capability_registry = config.get_capability_registry()
    capability_urls = config.get_capability_service_urls()
    if capability_urls:
        try:
            await capability_registry.discover(capability_urls)
            _log.info(
                "Capability registry: %d service(s) registered, %d tool(s) available",
                len(capability_registry.all_services()),
                len(capability_registry.get_tools()),
            )
        except Exception as exc:
            _log.warning("Capability registry initial discovery failed: %s", exc)

        try:
            await capability_registry.check_all_health()
            healthy = sum(1 for s in capability_registry.all_services() if s.healthy)
            _log.info(
                "Capability registry: initial health probe complete (%d/%d healthy)",
                healthy,
                len(capability_registry.all_services()),
            )
        except Exception as exc:
            _log.warning("Capability registry initial health probe failed: %s", exc)
    else:
        _log.info("Capability registry: no CAPABILITY_SERVICE_URLS configured")

    # Capability registry refresh — picks up services that came online
    # after Core started, and refreshes manifests for already-registered
    # services. Job is a sync wrapper around discover_sync().
    if scheduler:
        def _refresh_capability_registry() -> None:
            capability_registry.discover_sync(config.get_capability_service_urls())

        scheduler.add_job(
            _refresh_capability_registry,
            trigger="interval",
            minutes=5,
            id="capability_registry_refresh",
            name="Capability registry refresh",
            replace_existing=True,
            misfire_grace_time=60,
            coalesce=True,
            max_instances=1,
        )
        _log.info("Capability registry refresh job registered (every 5 minutes)")

        # Capability service health probe — faster cadence than discovery
        # because health is cheap and operators want fresh status. Updates
        # `healthy` and `last_seen_healthy` on each RegisteredService in place.
        def _probe_capability_health() -> None:
            capability_registry.check_all_health_sync()

        scheduler.add_job(
            _probe_capability_health,
            trigger="interval",
            seconds=60,
            id="capability_health_check",
            name="Capability service health probes",
            replace_existing=True,
            misfire_grace_time=30,
            coalesce=True,
            max_instances=1,
        )
        _log.info("Capability service health probe job registered (every 60 seconds)")

    from librechat_config import generate_librechat_yaml

    if generate_librechat_yaml():
        import httpx as _httpx
        from routes.admin import _current_restart_secret

        _sc_url = os.environ.get("STACK_CONTROL_URL", "http://stack-control:9000")
        try:
            _httpx.post(
                f"{_sc_url}/restart",
                json={"services": ["librechat"]},
                headers={"X-Lumogis-Restart-Token": _current_restart_secret()},
                timeout=30,
            )
            _log.info("LibreChat restarted with generated config")
        except Exception as exc:
            _log.warning("Could not restart LibreChat: %s", exc)

    _log.info("Startup complete")
    yield

    from services.ingest import stop_watcher

    stop_watcher()

    from signals import stop_all as stop_signal_monitors

    stop_signal_monitors()
    scheduler.shutdown(wait=False)
    _log.info("APScheduler shutdown")

    hooks.shutdown()
    config.shutdown()
    _log.info("Shutdown complete")


app = FastAPI(title="Lumogis", description="Personal AI assistant orchestrator", lifespan=lifespan)
app.middleware("http")(auth_middleware)
app.include_router(chat_router)
app.include_router(admin_router)
app.include_router(data_router)
app.include_router(signals_router)
app.include_router(actions_router)
app.include_router(events_router)
