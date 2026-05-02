# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""KG-service config: env-driven singletons + DB-first parameter getters.

This is a vendored, trimmed slice of Core's `orchestrator/config.py`. It
exposes only what the standalone lumogis-graph service needs at runtime:

  - Adapter singletons: `get_metadata_store`, `get_graph_store`,
    `get_vector_store` (returns None unless `VECTOR_STORE_BACKEND=qdrant`),
    `get_embedder` (returns None unless `EMBEDDER_BACKEND` is set).
  - The shared APScheduler instance (`get_scheduler`).
  - DB-first / env-fallback graph parameter getters used by the writer,
    reconcile, query, and quality modules (with the same names and
    defaults Core uses, so reading the same `kg_settings` table or the
    same `.env` produces identical behaviour).
  - `get_kg_webhook_secret()` for the inbound `/webhook` and `/context`
    bearer-token check.

What is INTENTIONALLY NOT here:
  - LLM provider getters, model catalog, ollama discovery, optional-model
    gating — KG never invokes an LLM.
  - Reranker, OCR, watchdog, notifier — none of those touch the graph.
  - Capability registry — KG is registered AT, not registering INTO.

Reading `kg_settings` requires Postgres; the TTL cache is the same as
Core's so a `POST /kg/settings` write to either side is visible to the
other within 30 s without explicit invalidation.
"""

import logging
import os
import threading
import time
from pathlib import Path

from ports.metadata_store import MetadataStore  # noqa: F401  (re-exported)

_log = logging.getLogger(__name__)
_instances: dict[str, object] = {}


# ---------------------------------------------------------------------------
# kg_settings TTL cache — copy of Core's mechanism for parity (same key, same
# TTL, same fail-soft behaviour, same `invalidate_settings_cache()` API).
# ---------------------------------------------------------------------------

_settings_cache: dict[str, str] = {}
_settings_cache_loaded_at: float = 0.0
_settings_cache_lock = threading.Lock()
_SETTINGS_TTL = 30.0  # seconds


def _get_setting(key: str, default: str) -> str:
    """Read a setting from the kg_settings table (TTL-cached, 30 s).

    Falls back to *default* if Postgres is unavailable or the key is
    missing. Never raises — callers must always get a usable value.
    """
    global _settings_cache, _settings_cache_loaded_at

    now = time.monotonic()
    with _settings_cache_lock:
        if now - _settings_cache_loaded_at < _SETTINGS_TTL:
            return _settings_cache.get(key, default)

    try:
        ms = get_metadata_store()
        rows = ms.fetch_all("SELECT key, value FROM kg_settings")
        new_cache = {r["key"]: r["value"] for r in rows}
        with _settings_cache_lock:
            _settings_cache = new_cache
            _settings_cache_loaded_at = time.monotonic()
        return new_cache.get(key, default)
    except Exception:
        _log.warning(
            "kg_settings: failed to read from Postgres — using env/default fallback for key=%r",
            key,
        )
        return default


def invalidate_settings_cache() -> None:
    """Force the next `_get_setting` call to re-fetch from Postgres."""
    global _settings_cache_loaded_at
    with _settings_cache_lock:
        _settings_cache_loaded_at = 0.0


# ---------------------------------------------------------------------------
# stop_entities.txt — mtime-based reload (used by quality/entity_quality.py)
# ---------------------------------------------------------------------------

_stop_entity_set: set[str] = set()
_stop_entity_mtime: float | None = None


def _resolve_config_file(name: str) -> str:
    """Locate a config file: bind-mounted /app/config first, then baked /opt/lumogis/config."""
    candidates = [
        Path(__file__).resolve().parent / "config" / name,
        Path("/opt/lumogis/config") / name,
    ]
    for p in candidates:
        if p.is_file():
            return str(p)
    return str(candidates[0])


def get_stop_entities_path() -> str:
    return os.environ.get("STOP_ENTITIES_PATH") or _resolve_config_file("stop_entities.txt")


def get_stop_entity_set() -> set[str]:
    """Return a cached lowercased stop-phrase set; reloads on file mtime change."""
    global _stop_entity_set, _stop_entity_mtime

    path_str = get_stop_entities_path()
    try:
        mtime = os.path.getmtime(path_str)
    except OSError:
        if _stop_entity_mtime is None:
            _log.warning(
                "Stop entity list not found or unreadable at %r — using empty stop set",
                path_str,
            )
        return _stop_entity_set

    if mtime == _stop_entity_mtime:
        return _stop_entity_set

    try:
        phrases: set[str] = set()
        with open(path_str, encoding="utf-8") as fh:
            for line in fh:
                stripped = line.strip()
                if stripped and not stripped.startswith("#"):
                    phrases.add(stripped.lower())
        _stop_entity_set = phrases
        _stop_entity_mtime = mtime
        _log.info("Stop entity list loaded: %d phrases from %r", len(phrases), path_str)
    except Exception:
        _log.warning(
            "Failed to read stop entity list from %r — using previous set (%d phrases)",
            path_str,
            len(_stop_entity_set),
            exc_info=True,
        )
    return _stop_entity_set


# ---------------------------------------------------------------------------
# Adapter singletons
# ---------------------------------------------------------------------------


def get_metadata_store():
    if "metadata_store" not in _instances:
        from adapters.postgres_store import PostgresStore

        _instances["metadata_store"] = PostgresStore(
            host=os.environ.get("POSTGRES_HOST", "postgres"),
            port=int(os.environ.get("POSTGRES_PORT", "5432")),
            user=os.environ.get("POSTGRES_USER", "lumogis"),
            password=os.environ.get("POSTGRES_PASSWORD", "lumogis-dev"),
            dbname=os.environ.get("POSTGRES_DB", "lumogis"),
        )
    return _instances["metadata_store"]


def get_graph_store():
    """Return the GraphStore singleton, or None if `GRAPH_BACKEND != "falkordb"`.

    The KG service is graph-only; if the operator hasn't configured a
    graph backend, every writer/reader path no-ops cleanly.
    """
    if "graph_store" not in _instances:
        backend = os.environ.get("GRAPH_BACKEND", "falkordb")
        if backend == "falkordb":
            from adapters.falkordb_store import FalkorDBStore

            url = os.environ.get("FALKORDB_URL", "redis://falkordb:6379")
            graph_name = os.environ.get("FALKORDB_GRAPH_NAME", "lumogis")
            _instances["graph_store"] = FalkorDBStore(url=url, graph_name=graph_name)
            _log.info("GraphStore: FalkorDB at %s graph=%s", url, graph_name)
        else:
            _instances["graph_store"] = None
            _log.info("GraphStore: disabled (GRAPH_BACKEND=%s)", backend)
    return _instances.get("graph_store")


def get_vector_store():
    """Return the VectorStore singleton, or None if not configured.

    The KG service does NOT pull qdrant-client into its base
    `requirements.txt`. Operators who want dedup ANN blocking active in
    KG must explicitly opt in by installing qdrant-client into the image
    AND setting `VECTOR_STORE_BACKEND=qdrant`. Default is None and dedup
    falls back to type-block + attr-block only.
    """
    if "vector_store" not in _instances:
        backend = os.environ.get("VECTOR_STORE_BACKEND", "none")
        if backend == "qdrant":
            try:
                from adapters.qdrant_store import QdrantStore  # type: ignore[import-not-found]

                _instances["vector_store"] = QdrantStore(
                    url=os.environ.get("QDRANT_URL", "http://qdrant:6333"),
                )
            except ImportError:
                _log.warning(
                    "VectorStore: VECTOR_STORE_BACKEND=qdrant but qdrant-client / "
                    "adapters.qdrant_store not installed — falling back to None",
                )
                _instances["vector_store"] = None
        else:
            _instances["vector_store"] = None
    return _instances.get("vector_store")


def get_embedder():
    """Return the Embedder singleton, or None if not configured.

    Same opt-in posture as `get_vector_store`: KG's base image has no
    embedder. Operators wiring up dedup ANN blocking must install the
    embedder dep (e.g. `requests` for `OllamaEmbedder`) and set
    `EMBEDDER_BACKEND` explicitly.
    """
    if "embedder" not in _instances:
        backend = os.environ.get("EMBEDDER_BACKEND", "none")
        if backend == "ollama":
            try:
                from adapters.ollama_embedder import OllamaEmbedder  # type: ignore[import-not-found]

                _instances["embedder"] = OllamaEmbedder(
                    url=os.environ.get("OLLAMA_URL", "http://ollama:11434"),
                    model=os.environ.get("EMBEDDING_MODEL", "nomic-embed-text"),
                )
            except ImportError:
                _log.warning(
                    "Embedder: EMBEDDER_BACKEND=ollama but adapters.ollama_embedder "
                    "not installed — falling back to None",
                )
                _instances["embedder"] = None
        else:
            _instances["embedder"] = None
    return _instances.get("embedder")


def get_scheduler():
    """Return the shared APScheduler BackgroundScheduler singleton.

    Construction only — `start()` is called from the FastAPI lifespan in
    `main.py` so startup ordering stays explicit.
    """
    if "scheduler" not in _instances:
        from apscheduler.schedulers.background import BackgroundScheduler

        scheduler = BackgroundScheduler(
            job_defaults={
                "misfire_grace_time": 60,
                "coalesce": True,
                "max_instances": 1,
            }
        )
        _instances["scheduler"] = scheduler
        _log.info("APScheduler BackgroundScheduler created")
    return _instances["scheduler"]


# ---------------------------------------------------------------------------
# Graph parameter getters (DB-first, env-var fallback) — mirror Core 1:1.
# ---------------------------------------------------------------------------


def get_entity_quality_lower() -> float:
    raw = _get_setting("entity_quality_lower", os.environ.get("ENTITY_QUALITY_LOWER", "0.35"))
    try:
        return float(raw)
    except (ValueError, TypeError):
        return 0.35


def get_entity_quality_upper() -> float:
    raw = _get_setting("entity_quality_upper", os.environ.get("ENTITY_QUALITY_UPPER", "0.60"))
    try:
        return float(raw)
    except (ValueError, TypeError):
        return 0.60


def get_entity_promote_on_mention_count() -> int:
    raw = _get_setting(
        "entity_promote_on_mention_count",
        os.environ.get("ENTITY_PROMOTE_ON_MENTION_COUNT", "3"),
    )
    try:
        return int(raw)
    except (ValueError, TypeError):
        return 3


def get_entity_quality_fail_open() -> bool:
    raw = _get_setting(
        "entity_quality_fail_open",
        os.environ.get("ENTITY_QUALITY_FAIL_OPEN", "true"),
    )
    return raw.strip().lower() in ("1", "true", "yes", "on")


def get_graph_edge_quality_threshold() -> float:
    raw = _get_setting(
        "graph_edge_quality_threshold",
        os.environ.get("GRAPH_EDGE_QUALITY_THRESHOLD", "0.3"),
    )
    try:
        return float(raw)
    except (ValueError, TypeError):
        return 0.3


def get_edge_quality_threshold() -> float:
    """Backward-compatible alias for `get_graph_edge_quality_threshold`."""
    return get_graph_edge_quality_threshold()


def get_cooccurrence_threshold() -> int:
    raw = _get_setting(
        "graph_cooccurrence_threshold",
        os.environ.get("GRAPH_COOCCURRENCE_THRESHOLD", "3"),
    )
    try:
        return int(raw)
    except (ValueError, TypeError):
        return 3


def get_graph_min_mention_count() -> int:
    raw = _get_setting(
        "graph_min_mention_count",
        os.environ.get("GRAPH_MIN_MENTION_COUNT", "2"),
    )
    try:
        return int(raw)
    except (ValueError, TypeError):
        return 2


def get_graph_max_cooccurrence_pairs() -> int:
    raw = _get_setting(
        "graph_max_cooccurrence_pairs",
        os.environ.get("GRAPH_MAX_COOCCURRENCE_PAIRS", "100"),
    )
    try:
        return int(raw)
    except (ValueError, TypeError):
        return 100


def get_graph_viz_max_nodes() -> int:
    raw = _get_setting(
        "graph_viz_max_nodes",
        os.environ.get("GRAPH_VIZ_MAX_NODES", "150"),
    )
    try:
        return int(raw)
    except (ValueError, TypeError):
        return 150


def get_graph_viz_max_edges() -> int:
    raw = _get_setting(
        "graph_viz_max_edges",
        os.environ.get("GRAPH_VIZ_MAX_EDGES", "300"),
    )
    try:
        return int(raw)
    except (ValueError, TypeError):
        return 300


def get_decay_half_life_relates_to() -> int:
    raw = _get_setting(
        "decay_half_life_relates_to",
        os.environ.get("DECAY_HALF_LIFE_RELATES_TO", "365"),
    )
    try:
        return int(raw)
    except (ValueError, TypeError):
        return 365


def get_decay_half_life_mentions() -> int:
    raw = _get_setting(
        "decay_half_life_mentions",
        os.environ.get("DECAY_HALF_LIFE_MENTIONS", "180"),
    )
    try:
        return int(raw)
    except (ValueError, TypeError):
        return 180


def get_decay_half_life_discussed_in() -> int:
    raw = _get_setting(
        "decay_half_life_discussed_in",
        os.environ.get("DECAY_HALF_LIFE_DISCUSSED_IN", "30"),
    )
    try:
        return int(raw)
    except (ValueError, TypeError):
        return 30


def get_dedup_cron_hour_utc() -> int:
    raw = _get_setting(
        "dedup_cron_hour_utc",
        os.environ.get("DEDUP_CRON_HOUR_UTC", "2"),
    )
    try:
        return int(raw)
    except (ValueError, TypeError):
        return 2


# ---------------------------------------------------------------------------
# Inbound webhook auth (KG-side counterpart of Core's get_kg_webhook_secret)
# ---------------------------------------------------------------------------


def get_kg_webhook_secret() -> str | None:
    """Return the bearer token KG accepts on `/webhook` and `/context`, or None.

    Mirrors Core's helper so a single shared `.env` works for both
    processes. Whitespace is stripped; empty strings collapse to None.
    """
    raw = os.environ.get("GRAPH_WEBHOOK_SECRET", "").strip()
    return raw or None


def kg_allow_insecure_webhooks() -> bool:
    """Return True iff `KG_ALLOW_INSECURE_WEBHOOKS=true`.

    Used by `routes/webhook.py` and `routes/context.py` to decide
    whether unauthenticated callers are accepted when no
    `GRAPH_WEBHOOK_SECRET` is configured. Default is False (return 503)
    so a forgotten secret cannot silently expose the service.
    """
    raw = os.environ.get("KG_ALLOW_INSECURE_WEBHOOKS", "false").strip().lower()
    return raw in ("1", "true", "yes", "on")


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


def shutdown() -> None:
    """Close adapter connections and clear singletons. Idempotent."""
    store = _instances.get("metadata_store")
    if store and hasattr(store, "close"):
        try:
            store.close()  # type: ignore[union-attr]
        except Exception:
            _log.exception("config.shutdown: metadata_store.close failed")
    _instances.clear()
    _log.info("config.shutdown: all KG adapter instances released")
