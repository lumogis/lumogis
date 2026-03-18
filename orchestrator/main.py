"""
Lumogis orchestrator – FastAPI app.

App creation, lifespan (health checks, collection init, shutdown),
and router includes. All endpoint logic lives in routes/.
"""

import logging
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
# "signals" stores embedded content summaries for semantic dedup (Chunk 12a).
# Vector size 768 matches Nomic Embed. Changing later requires drop + re-index.
_COLLECTIONS = ["documents", "conversations", "entities", "email", "signals"]


@asynccontextmanager
async def lifespan(app: FastAPI):
    _log.info("Startup: pinging backends...")
    backends = {
        "vector_store": config.get_vector_store(),
        "metadata_store": config.get_metadata_store(),
        "embedder": config.get_embedder(),
    }
    for name, backend in backends.items():
        if not backend.ping():
            raise RuntimeError(
                f"STARTUP FAILED: {name} ({type(backend).__name__}) is unreachable. "
                f"Check the service and connection settings in .env."
            )
        _log.info("  %s (%s): OK", name, type(backend).__name__)

    embedder = backends["embedder"]
    vs = backends["vector_store"]
    dim = embedder.vector_size
    _log.info("Embedder vector size: %d", dim)
    for coll in _COLLECTIONS:
        vs.create_collection(coll, dim)

    reranker = config.get_reranker()
    if reranker is not None:
        reranker.warmup()
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
