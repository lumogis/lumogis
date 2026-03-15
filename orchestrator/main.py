"""
Lumogis orchestrator – FastAPI app.

App creation, lifespan (health checks, collection init, shutdown),
and router includes. All endpoint logic lives in routes/.
"""

import logging
from contextlib import asynccontextmanager

import hooks
from fastapi import FastAPI
from plugins import load_plugins
from routes.admin import router as admin_router
from routes.chat import router as chat_router
from routes.data import router as data_router

import config

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
_log = logging.getLogger(__name__)

# 4 collections, created on startup. Email is Phase 3 but schema is locked now.
# Vector size 768 matches Nomic Embed. Changing later requires drop + re-index.
_COLLECTIONS = ["documents", "conversations", "entities", "email"]


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

    loaded = load_plugins()
    if loaded:
        _log.info("Plugins loaded: %s", loaded)

    _log.info("Startup complete")
    yield

    hooks.shutdown()
    config.shutdown()
    _log.info("Shutdown complete")


app = FastAPI(title="Lumogis", description="Personal AI assistant orchestrator", lifespan=lifespan)
app.include_router(chat_router)
app.include_router(admin_router)
app.include_router(data_router)
