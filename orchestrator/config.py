# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""
Wiring layer: reads .env, returns cached adapter singletons.

Every get_*() call returns the same object after first construction.
Call shutdown() during app teardown to close connections.
"""

import ipaddress
import json
import logging
import os
import re
import threading
import time
from functools import cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse
from urllib.parse import urlunparse

import yaml
from ports.embedder import Embedder
from ports.llm_provider import LLMProvider
from ports.metadata_store import MetadataStore
from ports.vector_store import VectorStore

_log = logging.getLogger(__name__)
_instances: dict[str, object] = {}
_effective_graph_mode: str | None = None
_graph_store_import_warning_emitted: bool = False
_models_config: dict | None = None

# ---------------------------------------------------------------------------
# kg_settings TTL cache (hot-reload KG parameters)
# ---------------------------------------------------------------------------

_settings_cache: dict[str, str] = {}
_settings_cache_loaded_at: float = 0.0
_settings_cache_lock = threading.Lock()
_SETTINGS_TTL = 30.0  # seconds


def _get_setting(key: str, default: str) -> str:
    """Read a setting from kg_settings table (TTL-cached, 30 s).

    Falls back to *default* if the key is not in the DB or if Postgres is
    unavailable.  Never raises.  Internal use only — callers are the typed
    getter functions below.
    """
    global _settings_cache, _settings_cache_loaded_at

    now = time.monotonic()
    with _settings_cache_lock:
        if now - _settings_cache_loaded_at < _SETTINGS_TTL:
            return _settings_cache.get(key, default)

    # Cache expired — re-fetch outside the lock to avoid blocking callers
    # during a slow DB query.  A double-fetch on cache expiry is acceptable.
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
    """Force the next _get_setting call to re-fetch from Postgres.

    Called by POST /kg/settings after a successful write so the new value
    is visible on the very next request.
    """
    global _settings_cache_loaded_at
    with _settings_cache_lock:
        _settings_cache_loaded_at = 0.0


# ---------------------------------------------------------------------------
# Stop entity list cache (mtime-based invalidation)
# ---------------------------------------------------------------------------

_stop_entity_set: set[str] = set()
_stop_entity_mtime: float | None = None


def get_stop_entities_path() -> str:
    """Return the resolved filesystem path to the stop_entities.txt file.

    Respects STOP_ENTITIES_PATH env var; falls back to _resolve_config_file().
    Does not check whether the file exists — callers handle missing files.
    """
    return os.environ.get("STOP_ENTITIES_PATH") or _resolve_config_file("stop_entities.txt")


def get_stop_entity_set() -> set[str]:
    """Return a cached set of lowercased stop phrases from stop_entities.txt.

    The file is re-read only when its mtime changes.  On missing/unreadable
    file: logs a WARNING once and returns an empty set.  Never raises.
    """
    global _stop_entity_set, _stop_entity_mtime

    path_str = get_stop_entities_path()
    try:
        mtime = os.path.getmtime(path_str)
    except OSError:
        if _stop_entity_mtime is not None:
            # Previously loaded; file disappeared — keep the cached set.
            return _stop_entity_set
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


def _resolve_config_file(name: str) -> str:
    """Return a readable file path for a config file.

    Tries the bind-mounted location (/app/config/) first, then the
    image-baked fallback (/opt/lumogis/config/).  Docker bind mounts
    can create empty directories instead of files on some host
    configurations, so we verify the path is a regular file.
    """
    candidates = [
        Path(__file__).resolve().parent / "config" / name,  # /app/config/<name>
        Path("/opt/lumogis/config") / name,  # baked into image
    ]
    for p in candidates:
        if p.is_file():
            return str(p)
    return str(candidates[0])


def _load_models_yaml() -> dict:
    """Load and cache config/models.yaml."""
    global _models_config
    if _models_config is None:
        config_path = os.environ.get("MODELS_CONFIG", _resolve_config_file("models.yaml"))
        with open(config_path) as f:
            _models_config = yaml.safe_load(f).get("models", {})
    return _models_config


_OLLAMA_BASE_URL = os.environ.get("OLLAMA_URL", "http://ollama:11434").rstrip("/") + "/v1"


def _dynamic_ollama_models() -> dict:
    """Return synthetic config entries for Ollama models not in models.yaml.

    Queries Ollama for installed models and creates entries for any that
    aren't already defined in the YAML. This lets users pull models via
    the dashboard and immediately use them in LibreChat.
    """
    try:
        import ollama_client

        local = ollama_client.list_local_models()
    except Exception:
        return {}

    yaml_models = _load_models_yaml()
    yaml_ollama_names = set()
    for cfg in yaml_models.values():
        if "ollama" in (cfg.get("base_url") or "").lower():
            yaml_ollama_names.add(cfg.get("model", ""))

    embedding_model = os.environ.get("EMBEDDING_MODEL", "nomic-embed-text")
    dynamic: dict = {}
    for m in local:
        full_name = m.get("name", "")
        base_name = full_name.split(":")[0]
        if full_name in yaml_ollama_names or base_name in yaml_ollama_names:
            continue
        if base_name == embedding_model or full_name == embedding_model:
            continue
        alias = base_name
        if alias in yaml_models or alias in dynamic:
            alias = full_name.replace(":", "-")
        dynamic[alias] = {
            "adapter": "openai",
            "model": full_name,
            "base_url": _OLLAMA_BASE_URL,
            "tools": False,
            "dynamic_ollama": True,
        }
    return dynamic


def get_all_models_config() -> dict:
    """Return models from YAML merged with dynamically pulled Ollama models."""
    merged = dict(_load_models_yaml())
    merged.update(_dynamic_ollama_models())
    return merged


def get_model_config(model_name: str) -> dict:
    """Return a single model's config entry (YAML or dynamic Ollama)."""
    models = _load_models_yaml()
    if model_name in models:
        return models[model_name]
    dynamic = _dynamic_ollama_models()
    if model_name in dynamic:
        return dynamic[model_name]
    raise ValueError(f"Unknown model '{model_name}'. Available: {list(models.keys())}")


def invalidate_llm_cache() -> None:
    """Remove **legacy** cached LLM provider instances.

    Targets the pre-migration cache keys (``"llm_<model_name>"`` —
    underscore separator, single shared slot per model). Kept for
    backward compatibility with the auth-off ``PUT /api/v1/admin/settings``
    code path which still flushes the global cache after writing a key
    into ``app_settings``.

    Under ``AUTH_ENABLED=true`` the per-user invalidator
    :func:`invalidate_llm_cache_for_user` (driven by the connector
    credentials change-listener mechanism) handles eviction; this
    function becomes a no-op there because no entries match the
    legacy underscore prefix.
    """
    to_drop = [k for k in _instances if k.startswith("llm_")]
    for k in to_drop:
        del _instances[k]
    if to_drop:
        _log.info("LLM cache invalidated (legacy): %s", to_drop)


def invalidate_llm_cache_for_user(user_id: str | None) -> None:
    """Drop per-user LLM adapters from the cache.

    The new cache key shape (set by :func:`get_llm_provider`) is
    ``"llm:<user_id or '_global'>:<model_name>"`` for cloud models
    and ``"llm:_local:<model_name>"`` for local models. Pass
    ``user_id`` to drop one user's cloud entries; pass ``None`` to
    drop the legacy/global slot only (auth-off mode).

    Local model entries are **never** evicted by this helper —
    they hold no per-user secret material. The change-listener
    registered at the bottom of this module funnels every
    credential mutation through here.
    """
    target = user_id if user_id else "_global"
    prefix = f"llm:{target}:"
    to_drop = [k for k in _instances if k.startswith(prefix)]
    for k in to_drop:
        del _instances[k]
    if to_drop:
        _log.info("LLM cache invalidated (per-user=%s): %s", target, to_drop)


def is_local_model(model_name: str) -> bool:
    """True if the model runs locally (e.g. via Ollama); used for loading hints."""
    try:
        cfg = get_model_config(model_name)
        base = (cfg.get("base_url") or "").lower()
        return "ollama" in base
    except ValueError:
        return False


def is_model_enabled(
    model_name: str,
    *,
    user_id: str | None = None,
    _credentials_present: set[str] | None = None,
) -> bool:
    """True if the model is available for use.

    Plan ``llm_provider_keys_per_user_migration`` Pass 2.5: extended
    with ``user_id`` (per-user resolution under
    ``AUTH_ENABLED=true``) and ``_credentials_present`` (perf hint
    for ``/v1/models`` to avoid N point queries — single underscore
    prefix marks it as not-public and exempt from semver).

    Rules:

    - Unknown model (not in YAML): always False.
    - Models without ``api_key_env`` (local Ollama models): always
      enabled regardless of ``user_id``.
    - Optional models (``optional: true`` in YAML): require an
      explicit household toggle stored as ``app_setting`` key
      ``"optional_{model_name}" = "true"`` (kept as a household
      admin gate per question 10) in addition to a key. The
      household toggle is checked first under both auth modes; if
      off, the function returns False without consulting per-user
      rows / env.
    - Non-optional cloud models (``api_key_env`` set):
        * Under ``AUTH_ENABLED=false``: enabled iff the legacy
          ``app_settings`` key is non-empty (with env-var fallback).
        * Under ``AUTH_ENABLED=true``: enabled iff the per-user row
          exists. ``user_id is None`` ⇒ False (no user → no key →
          not enabled). Never raises — admin/dashboard callers like
          ``_safe_is_enabled`` poll without a user context.

    The ``_credentials_present`` hint, when supplied, replaces the
    per-call ``has_credential`` lookup with a set-membership check.
    Pass ``llm_connector_map.get_user_credentials_snapshot(user_id)``
    once per request and reuse for every model.
    """
    try:
        cfg = get_model_config(model_name)
    except ValueError:
        return False

    api_key_env = cfg.get("api_key_env", "")
    if not api_key_env:
        return True

    from auth import auth_enabled

    # Household optional toggle is checked first in both auth modes.
    if cfg.get("optional"):
        from settings_store import get_setting

        store = get_metadata_store()
        toggled = get_setting(f"optional_{model_name}", store)
        if not (toggled and toggled.lower() in ("true", "1", "yes")):
            return False

    if auth_enabled():
        if not user_id:
            return False
        from services.llm_connector_map import connector_for_api_key_env
        from services.llm_connector_map import has_credential

        if _credentials_present is not None:
            connector = connector_for_api_key_env(api_key_env)
            return connector is not None and connector in _credentials_present
        return has_credential(user_id, api_key_env)

    # Auth-off: legacy global app_settings + env path.
    from settings_store import get_setting

    store = get_metadata_store()
    stored_key = get_setting(api_key_env, store)
    effective_key = (stored_key or os.environ.get(api_key_env, "") or "").strip()
    return bool(effective_key)


def get_llm_provider(
    model_name: str,
    *,
    user_id: str | None = None,
) -> LLMProvider:
    """Return a cached LLMProvider adapter for the given model name.

    Plan ``llm_provider_keys_per_user_migration`` Pass 2.5: cache
    keys are now per-user for cloud models and shared for local
    models:

    * Cloud (``api_key_env`` set):
      ``"llm:<user_id or '_global'>:<model_name>"``. The
      ``_global`` slot is reachable **only** under
      ``AUTH_ENABLED=false`` (legacy single-user). Under auth-on,
      ``user_id is None`` raises :class:`TypeError` — programmer
      error to be loud about, not a runtime user-facing 4xx.
    * Local (no ``api_key_env``):
      ``"llm:_local:<model_name>"``. Single shared adapter
      regardless of caller — local models hold no per-user secret
      material.

    Credential resolution for cloud models is delegated to
    :func:`services.llm_connector_map.effective_api_key`, which
    raises :class:`ConnectorNotConfigured` (mapped to HTTP 424 by
    the chat route) or :class:`CredentialUnavailable` (mapped to
    HTTP 503) on the documented failure shapes. ``get_llm_provider``
    propagates both unchanged.
    """
    cfg = get_model_config(model_name)
    api_key_env = cfg.get("api_key_env", "")

    from auth import auth_enabled

    if api_key_env:
        if auth_enabled() and not user_id:
            raise TypeError(
                "get_llm_provider: user_id (keyword-only) is required for "
                f"cloud model '{model_name}' when AUTH_ENABLED=true"
            )
        cache_key = f"llm:{user_id or '_global'}:{model_name}"
    else:
        cache_key = f"llm:_local:{model_name}"

    if cache_key in _instances:
        return _instances[cache_key]  # type: ignore[return-value]

    proxy = cfg.get("proxy_url")
    adapter_type = cfg["adapter"]

    if api_key_env:
        from services.llm_connector_map import effective_api_key

        effective_key = effective_api_key(user_id, api_key_env)
    else:
        effective_key = ""

    if adapter_type == "anthropic":
        from adapters.anthropic_llm import AnthropicLLM

        _instances[cache_key] = AnthropicLLM(
            model=cfg["model"],
            api_key=effective_key or "",
            base_url=proxy,
        )
    elif adapter_type == "openai":
        from adapters.openai_llm import OpenAILLM

        _instances[cache_key] = OpenAILLM(
            model=cfg["model"],
            base_url=proxy or cfg.get("base_url"),
            api_key=effective_key or None,
            context_budget=cfg.get("context_budget"),
        )
    else:
        raise ValueError(f"Unknown adapter type '{adapter_type}' for model '{model_name}'")

    _log.info(
        "LLM provider created: %s (adapter=%s, model=%s, cache_key=%s)",
        model_name,
        adapter_type,
        cfg["model"],
        cache_key,
    )
    return _instances[cache_key]  # type: ignore[return-value]


def _check_background_model_defaults() -> None:
    """Boot-time gate against silent cloud-LLM background failures.

    Plan ``llm_provider_keys_per_user_migration`` Pass 2.6: under
    ``AUTH_ENABLED=true``, the **default** ``SIGNAL_LLM_MODEL`` (used
    by signal sources that do not happen to thread a per-user
    ``user_id``) MUST be a local model. A cloud default would
    silently disable signal LLM enrichment for every signal whose
    source omits ``user_id`` — no per-user key can be resolved
    without one.

    Behaviour matrix:

    * Unknown model → WARN (not RAISE) — the runtime ``_call_llm``
      branch already short-circuits gracefully when the model is
      not in ``models.yaml``.
    * Cloud model + ``AUTH_ENABLED=true`` → RAISE ``RuntimeError``
      with operator-actionable message (no env var threading can
      satisfy a process-wide default).
    * Local model OR ``AUTH_ENABLED=false`` → silent pass.

    Wired into ``main._enforce_auth_consistency``'s lifespan
    immediately after the existing auth-consistency gate so a
    misconfigured signal pipeline halts startup before the embedder
    ping or route registration.
    """
    from auth import auth_enabled

    model_name = os.environ.get("SIGNAL_LLM_MODEL", "llama")

    try:
        cfg = get_model_config(model_name)
    except ValueError:
        _log.warning(
            "SIGNAL_LLM_MODEL=%r is not in models.yaml; signal LLM enrichment "
            "will be disabled at runtime (process continues).",
            model_name,
        )
        return

    if cfg.get("api_key_env") and auth_enabled():
        raise RuntimeError(
            f"SIGNAL_LLM_MODEL is set to a cloud model ({model_name!r}) under "
            "AUTH_ENABLED=true. This default would silently disable signal LLM "
            "enrichment for any signal whose source did not happen to thread a "
            "user_id (the substrate cannot resolve a per-user API key without "
            "one). Set SIGNAL_LLM_MODEL to a local model to proceed (e.g. "
            "SIGNAL_LLM_MODEL=llama). Per-user signal sources that thread "
            "user_id at runtime can still target cloud models on a per-call "
            "basis — this boot check guards the *default* model only."
        )


def get_vector_store() -> VectorStore:
    if "vector_store" not in _instances:
        backend = os.environ.get("VECTOR_STORE_BACKEND", "qdrant")
        if backend == "qdrant":
            from adapters.qdrant_store import QdrantStore

            _instances["vector_store"] = QdrantStore(
                url=os.environ.get("QDRANT_URL", "http://qdrant:6333")
            )
        else:
            raise ValueError(f"Unknown vector store backend: {backend}")
    return _instances["vector_store"]  # type: ignore[return-value]


def get_metadata_store() -> MetadataStore:
    if "metadata_store" not in _instances:
        backend = os.environ.get("METADATA_STORE_BACKEND", "postgres")
        if backend == "postgres":
            from adapters.postgres_store import PostgresStore

            _instances["metadata_store"] = PostgresStore(
                host=os.environ.get("POSTGRES_HOST", "postgres"),
                port=int(os.environ.get("POSTGRES_PORT", "5432")),
                user=os.environ.get("POSTGRES_USER", "lumogis"),
                password=os.environ.get("POSTGRES_PASSWORD", "lumogis-dev"),
                dbname=os.environ.get("POSTGRES_DB", "lumogis"),
            )
        else:
            raise ValueError(f"Unknown metadata store backend: {backend}")
    return _instances["metadata_store"]  # type: ignore[return-value]


def get_embedder() -> Embedder:
    if "embedder" not in _instances:
        backend = os.environ.get("EMBEDDER_BACKEND", "ollama")
        if backend == "ollama":
            from adapters.ollama_embedder import OllamaEmbedder

            _instances["embedder"] = OllamaEmbedder(
                url=os.environ.get("OLLAMA_URL", "http://ollama:11434"),
                model=os.environ.get("EMBEDDING_MODEL", "nomic-embed-text"),
            )
        else:
            raise ValueError(f"Unknown embedder backend: {backend}")
    return _instances["embedder"]  # type: ignore[return-value]


def get_reranker():
    if "reranker" not in _instances:
        backend = os.environ.get("RERANKER_BACKEND", "none")
        if backend == "none":
            _instances["reranker"] = None
        elif backend == "bge":
            from adapters.bge_reranker import BGEReranker

            _instances["reranker"] = BGEReranker(
                model_name=os.environ.get("RERANKER_MODEL", "BAAI/bge-reranker-base"),
            )
        else:
            raise ValueError(f"Unknown reranker backend: {backend}")
    return _instances.get("reranker")


# --- Extractor auto-discovery ---

_extractor_registry: dict[str, callable] = {}

_ocr_adapters = {"ocr_extractor"}


def extractor(*extensions):
    """Decorator: registers a function as the extractor for given file extensions."""

    def decorator(fn):
        for ext in extensions:
            _extractor_registry[ext] = fn
        return fn

    return decorator


def get_extractors() -> dict[str, callable]:
    if "extractors" not in _instances:
        import importlib
        import pkgutil

        from adapters import __path__ as adapters_path

        ocr_enabled = os.environ.get("EXTRACTOR_OCR_ENABLED", "true").lower() == "true"
        for _, name, _ in pkgutil.iter_modules(adapters_path):
            if name in _ocr_adapters and not ocr_enabled:
                continue
            try:
                importlib.import_module(f"adapters.{name}")
            except ImportError:
                _log.debug("Skipping adapter %s (missing deps)", name)
        _instances["extractors"] = dict(_extractor_registry)
    return _instances["extractors"]


def get_notifier():
    """Return a cached Notifier adapter.

    NOTIFIER_BACKEND env var:
      "ntfy"  -> NtfyNotifier (posts to ntfy server at NTFY_URL)
      "none"  -> NullNotifier (no-op, default)
    """
    if "notifier" not in _instances:
        backend = os.environ.get("NOTIFIER_BACKEND", "none")
        if backend == "ntfy":
            from adapters.ntfy_notifier import NtfyNotifier

            _instances["notifier"] = NtfyNotifier()
        else:
            from adapters.null_notifier import NullNotifier

            _instances["notifier"] = NullNotifier()
        _log.info("Notifier: %s (backend=%s)", type(_instances["notifier"]).__name__, backend)
    return _instances["notifier"]


def is_injection_sanitiser_enabled() -> bool:
    return os.environ.get("INJECTION_SANITISER_ENABLED", "true").strip().lower() in (
        "true",
        "1",
        "yes",
        "on",
    )


def get_tool_chain_cap() -> int:
    raw = os.environ.get("TOOL_CHAIN_CAP", "10")
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return 10


def get_injection_scanner():
    """Return cached injection scanner singleton (currently only ``null`` / passthrough).

    Mirrors :func:`get_notifier` extensibility: future scanners plug in behind
    ``INJECTION_SCANNER`` without refactoring call sites.
    """
    if "injection_scanner" not in _instances:
        backend = os.environ.get("INJECTION_SCANNER", "null").strip().lower()
        if backend != "null":
            raise RuntimeError(
                f"Unsupported INJECTION_SCANNER={backend!r} (only 'null' shipped in this release). "
                "If startup must proceed without this adapter, disable pattern loading via "
                "INJECTION_SANITISER_ENABLED=false in .env."
            )
        from adapters.null_injection_scanner import NullInjectionScanner

        _instances["injection_scanner"] = NullInjectionScanner()
        _log.info("Injection scanner adapter: NullInjectionScanner")
    return _instances["injection_scanner"]


def warmup_injection_sanitiser() -> None:
    """Eager-load YAML regex table + scanner before ``Startup complete``.

    Raises :class:`RuntimeError` carrying ``INJECTION_SANITISER_ENABLED=false``
    remediation when patterns are malformed or inaccessible.
    """
    if not is_injection_sanitiser_enabled():
        _log.info("Injection sanitiser disabled via INJECTION_SANITISER_ENABLED")
        return
    try:
        from services.injection_sanitiser import ensure_patterns_loaded

        ensure_patterns_loaded()
        get_injection_scanner().scan("")
    except Exception as exc:
        raise RuntimeError(
            f"Injection sanitiser startup failed: {exc}. "
            "Remediation: fix orchestrator/data/injection_patterns.yaml (or "
            "INJECTION_PATTERN_FILE) or set INJECTION_SANITISER_ENABLED=false in .env."
        ) from exc


def get_scheduler():
    """Return the shared APScheduler BackgroundScheduler singleton.

    The scheduler is created here but NOT started — call scheduler.start()
    from main.py lifespan so startup order is controlled.
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


def get_graph_store():
    """Return the GraphStore singleton, or None if GRAPH_BACKEND is not configured.

    Set GRAPH_BACKEND=falkordb and FALKORDB_URL=redis://falkordb:6379 to enable.
    When disabled (default), the graph plugin silently does nothing.

    If GRAPH_BACKEND=falkordb but the FalkorDB adapter cannot be imported,
    returns ``None`` after emitting a single ``graph_store_unavailable`` WARNING.
    """
    global _graph_store_import_warning_emitted

    if "graph_store" not in _instances:
        backend = os.environ.get("GRAPH_BACKEND", "none")
        if backend == "falkordb":
            try:
                from adapters.falkordb_store import FalkorDBStore

                url = os.environ.get("FALKORDB_URL", "redis://falkordb:6379")
                graph_name = os.environ.get("FALKORDB_GRAPH_NAME", "lumogis")
                _instances["graph_store"] = FalkorDBStore(url=url, graph_name=graph_name)
                _log.info("GraphStore: FalkorDB at %s graph=%s", url, graph_name)
            except ImportError as exc:
                _instances["graph_store"] = None
                if not _graph_store_import_warning_emitted:
                    _graph_store_import_warning_emitted = True
                    _log.warning(
                        "FalkorDB graph store unavailable — continuing without GraphStore",
                        extra={
                            "event": "graph_store_unavailable",
                            "reason": "falkordb_adapter_import_error",
                            "exc_type": type(exc).__name__,
                        },
                    )
        else:
            _instances["graph_store"] = None
            _log.info("GraphStore: disabled (GRAPH_BACKEND=%s)", backend)
    return _instances.get("graph_store")


# ---------------------------------------------------------------------------
# KG quality / graph parameter getters — DB-first, env-var fallback
# ---------------------------------------------------------------------------


def get_entity_quality_lower() -> float:
    """Entities scoring below this threshold are discarded immediately.

    DB key: entity_quality_lower. Env fallback: ENTITY_QUALITY_LOWER (0.35).
    """
    raw = _get_setting("entity_quality_lower", os.environ.get("ENTITY_QUALITY_LOWER", "0.35"))
    try:
        return float(raw)
    except (ValueError, TypeError):
        return 0.35


def get_entity_quality_upper() -> float:
    """Entities scoring between lower and upper are staged; at or above upper are normal.

    DB key: entity_quality_upper. Env fallback: ENTITY_QUALITY_UPPER (0.60).
    """
    raw = _get_setting("entity_quality_upper", os.environ.get("ENTITY_QUALITY_UPPER", "0.60"))
    try:
        return float(raw)
    except (ValueError, TypeError):
        return 0.60


def get_entity_promote_on_mention_count() -> int:
    """Staged entity is auto-promoted when mention_count reaches this value.

    DB key: entity_promote_on_mention_count. Env fallback: ENTITY_PROMOTE_ON_MENTION_COUNT (3).
    """
    raw = _get_setting(
        "entity_promote_on_mention_count",
        os.environ.get("ENTITY_PROMOTE_ON_MENTION_COUNT", "3"),
    )
    try:
        return int(raw)
    except (ValueError, TypeError):
        return 3


def get_entity_quality_fail_open() -> bool:
    """When True, scorer exceptions return original entities unchanged (fail-open).

    DB key: entity_quality_fail_open. Env fallback: ENTITY_QUALITY_FAIL_OPEN (true).
    """
    raw = _get_setting(
        "entity_quality_fail_open",
        os.environ.get("ENTITY_QUALITY_FAIL_OPEN", "true"),
    )
    return raw.strip().lower() in ("1", "true", "yes", "on")


def get_graph_edge_quality_threshold() -> float:
    """Minimum edge_quality score for RELATES_TO edges to appear in queries.

    Edges scored below this threshold are filtered from ego_network results.
    Edges with NULL edge_quality (pre-scoring) use co_occurrence_count gate only.

    DB key: graph_edge_quality_threshold. Env fallback: GRAPH_EDGE_QUALITY_THRESHOLD (0.3).
    """
    raw = _get_setting(
        "graph_edge_quality_threshold",
        os.environ.get("GRAPH_EDGE_QUALITY_THRESHOLD", "0.3"),
    )
    try:
        return float(raw)
    except (ValueError, TypeError):
        return 0.3


# Keep the old name as an alias so existing callers (query.py import-time read) don't break.
def get_edge_quality_threshold() -> float:
    """Alias for get_graph_edge_quality_threshold() — preserved for backward compatibility."""
    return get_graph_edge_quality_threshold()


def get_cooccurrence_threshold() -> int:
    """Minimum co-occurrence count before RELATES_TO edges are visible in queries/viz.

    DB key: graph_cooccurrence_threshold. Env fallback: GRAPH_COOCCURRENCE_THRESHOLD (3).
    """
    raw = _get_setting(
        "graph_cooccurrence_threshold",
        os.environ.get("GRAPH_COOCCURRENCE_THRESHOLD", "3"),
    )
    try:
        return int(raw)
    except (ValueError, TypeError):
        return 3


def get_graph_min_mention_count() -> int:
    """Entities mentioned fewer times than this are hidden from graph queries.

    DB key: graph_min_mention_count. Env fallback: GRAPH_MIN_MENTION_COUNT (2).
    """
    raw = _get_setting(
        "graph_min_mention_count",
        os.environ.get("GRAPH_MIN_MENTION_COUNT", "2"),
    )
    try:
        return int(raw)
    except (ValueError, TypeError):
        return 2


def _safe_int_env(var: str, default: int, *, minimum: int | None = None) -> int:
    raw = os.environ.get(var)
    if raw is None or str(raw).strip() == "":
        v = default
    else:
        try:
            v = int(str(raw).strip())
        except (ValueError, TypeError):
            _log.warning("Invalid int for %s=%r — using default %s", var, raw, default)
            v = default
    if minimum is not None:
        v = max(minimum, v)
    return v


def _safe_float_env(var: str, default: float) -> float:
    raw = os.environ.get(var)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return float(str(raw).strip())
    except (ValueError, TypeError):
        _log.warning("Invalid float for %s=%r — using default %s", var, raw, default)
        return default


def _env_bool(var: str, default: bool) -> bool:
    raw = os.environ.get(var)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def get_context_entity_budget() -> int:
    """Max entities considered for CONTEXT_BUILDING after ranking (default 5)."""
    return _safe_int_env("LUMOGIS_CONTEXT_ENTITY_BUDGET", 5, minimum=1)


def get_context_entity_semantic_enabled() -> bool:
    """When true, run optional Qdrant semantic top-up for CONTEXT_BUILDING."""
    return _env_bool("LUMOGIS_CONTEXT_BUILDER_SEMANTIC", False)


def get_context_entity_semantic_topk() -> int:
    return _safe_int_env("LUMOGIS_CONTEXT_BUILDER_SEMANTIC_TOPK", 5, minimum=1)


def get_context_entity_semantic_threshold() -> float:
    return _safe_float_env("LUMOGIS_CONTEXT_BUILDER_SEMANTIC_THRESHOLD", 0.75)


def get_context_entity_rank_alpha() -> float:
    return _safe_float_env("LUMOGIS_CONTEXT_ENTITY_RANK_ALPHA", 1.0)


def get_context_entity_rank_beta() -> float:
    return _safe_float_env("LUMOGIS_CONTEXT_ENTITY_RANK_BETA", 0.5)


def get_context_entity_rank_gamma() -> float:
    return _safe_float_env("LUMOGIS_CONTEXT_ENTITY_RANK_GAMMA", 0.0)


def get_context_entity_rank_delta() -> float:
    return _safe_float_env("LUMOGIS_CONTEXT_ENTITY_RANK_DELTA", 0.0)


def get_context_entity_explicit_bonus() -> float:
    return _safe_float_env("LUMOGIS_CONTEXT_ENTITY_EXPLICIT_BONUS", 1.0)


def get_auto_rag_enabled() -> bool:
    """When true, chat may inject document chunks before the LLM (LUM-308)."""
    return _env_bool("LUMOGIS_AUTO_RAG_ENABLED", False)


def get_auto_rag_top_k_pre() -> int:
    return _safe_int_env("LUMOGIS_AUTO_RAG_TOP_K_PRE", 20, minimum=1)


def get_auto_rag_top_k_post() -> int:
    return _safe_int_env("LUMOGIS_AUTO_RAG_TOP_K_POST", 3, minimum=1)


def get_auto_rag_min_rerank_score() -> float:
    return _safe_float_env("LUMOGIS_AUTO_RAG_MIN_RERANK_SCORE", 0.0)


def get_auto_rag_min_bi_encoder_score() -> float:
    """Dense cosine floor when reranker is off; ignored for RRF / hybrid scores."""
    return _safe_float_env("LUMOGIS_AUTO_RAG_MIN_BI_ENCODER_SCORE", 0.55)


def get_auto_rag_max_tokens() -> int:
    return _safe_int_env("LUMOGIS_AUTO_RAG_MAX_TOKENS", 512, minimum=1)


# ---------------------------------------------------------------------------
# LUM-330 — inbox watch / poll / quarantine
# ---------------------------------------------------------------------------

InboxMode = Literal["event", "poll", "off"]


def get_workspace_path() -> Path:
    """Resolved workspace root (``WORKSPACE_PATH``, default ``/workspace``)."""
    return Path(os.environ.get("WORKSPACE_PATH", "/workspace")).expanduser().resolve(strict=False)


def get_inbox_path() -> Path:
    """Pure inbox directory resolver — no mkdir (safe for ``/healthz``)."""
    raw = os.environ.get("LUMOGIS_INBOX_PATH", "/workspace/inbox").strip()
    candidate = Path(raw).expanduser()
    if candidate.is_absolute():
        return candidate.resolve(strict=False)
    return (get_workspace_path() / candidate).resolve(strict=False)


def get_quarantine_path() -> Path:
    """Quarantine directory beside inbox under workspace (not inside watched tree)."""
    return get_workspace_path() / "quarantine"


def get_uploads_path() -> Path:
    """Persistent push-ingest store under workspace (``uploads/`` by default)."""
    raw = os.environ.get("LUMOGIS_UPLOADS_PATH", "").strip()
    if raw:
        path = Path(raw).expanduser()
    else:
        path = get_workspace_path() / "uploads"
    resolved = path.resolve(strict=False)
    resolved.mkdir(parents=True, exist_ok=True)
    return resolved


_FORBIDDEN_INGEST_PATHS = frozenset({"/", "/etc", "/root"})


def _parse_json_path_list_env(name: str) -> list[str] | None:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        _log.warning("Invalid JSON in %s: %r", name, raw[:120])
        return None
    if not isinstance(parsed, list):
        _log.warning("%s must be a JSON array, got %r", name, type(parsed).__name__)
        return None
    out: list[str] = []
    for item in parsed:
        s = str(item).strip()
        if s:
            out.append(s)
    return out or None


def get_ingest_paths_allowed_prefixes() -> list[str]:
    """Comma-separated container path prefixes allowed for ingest_paths PUT validation."""
    raw = os.environ.get("INGEST_PATHS_ALLOWED_PREFIX", "/data").strip()
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    return parts or ["/data"]


def get_effective_ingest_paths() -> list[str]:
    """Container/runtime paths for search, tools, and ingest-path watchers."""
    paths = _parse_json_path_list_env("INGEST_PATHS")
    if paths:
        return paths
    fs = os.environ.get("FILESYSTEM_ROOT", "").strip()
    if fs:
        return [fs]
    return [str(Path.home())]


def get_effective_ingest_paths_host() -> list[str]:
    """Host/operator paths shown on GET /settings (INGEST_PATHS_HOST or FILESYSTEM_ROOT_HOST)."""
    paths = _parse_json_path_list_env("INGEST_PATHS_HOST")
    if paths:
        return paths
    host = os.environ.get("FILESYSTEM_ROOT_HOST", os.environ.get("FILESYSTEM_ROOT", "")).strip()
    if host:
        return [host]
    return []


def get_ingest_paths_owner_user_id() -> str:
    """User that owns files ingested from configured ingest paths."""
    explicit = os.environ.get("INGEST_PATHS_OWNER_USER_ID", "").strip()
    if explicit:
        return explicit
    return get_inbox_owner_user_id()


def get_ingest_paths_watch_mode() -> str:
    """``event`` (watchdog) or ``off`` — see ``INGEST_PATHS_WATCH_MODE``."""
    raw = os.environ.get("INGEST_PATHS_WATCH_MODE", "event").strip().lower()
    return raw if raw in ("event", "off") else "event"


def get_ingest_paths_watch_enabled() -> bool:
    """True when an owner is configured and watch mode is not ``off``."""
    if get_ingest_paths_watch_mode() == "off":
        return False
    return bool(get_ingest_paths_owner_user_id())


def migrate_filesystem_root_to_ingest_paths(store) -> None:
    """Wrap legacy app_settings ``filesystem_root`` into ``ingest_paths`` JSON once."""
    from settings_store import get_setting
    from settings_store import put_settings

    legacy = get_setting("filesystem_root", store)
    if not legacy or not legacy.strip():
        return
    if get_setting("ingest_paths", store):
        return
    put_settings(
        store,
        {
            "ingest_paths": json.dumps([legacy.strip()]),
            "filesystem_root": "",
        },
    )


def get_pending_ingest_paths(store) -> list[str] | None:
    """Pending ingest path list from app_settings (runs legacy migration first)."""
    from settings_store import get_setting

    migrate_filesystem_root_to_ingest_paths(store)
    raw = get_setting("ingest_paths", store)
    if raw is None or not str(raw).strip():
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        _log.warning("Invalid ingest_paths JSON in app_settings: %r", raw[:120])
        return None
    if not isinstance(parsed, list):
        return None
    out = [str(p).strip() for p in parsed if str(p).strip()]
    return out or None


def host_ingest_allowed_prefixes() -> list[str]:
    """Prefixes allowed for index>=1 host-picker paths inside the container."""
    prefixes = list(get_ingest_paths_allowed_prefixes())
    if Path("/host").exists():
        host_root = str(Path("/host").resolve(strict=False))
        if host_root not in prefixes:
            prefixes.append(host_root)
    return prefixes


def host_ingest_path_to_container(index: int, host_path: str) -> str:
    """Map stored ingest path to container path for INGEST_PATHS env."""
    if index == 0:
        return host_ingest_path_to_container_index0(host_path)
    from compose_ingest_binds import extra_container_path

    return extra_container_path(index)


def host_ingest_path_to_container_index0(host_path: str) -> str:
    """Map primary (host) ingest path to the container path used for allowlist checks.

    Index-0 paths are host/OS paths from the overlay; validation uses the in-container
    mount (``FILESYSTEM_ROOT``), not the raw host string.
    """
    _ = host_path  # browse/PUT may change host path before restart; mount stays until recreate
    return os.environ.get("FILESYSTEM_ROOT", "/data").strip() or "/data"


def resolved_path_under_prefix(path: str, prefixes: list[str] | None = None) -> bool:
    """True when *path* is under any allowed prefix (safe prefix, not naive startswith)."""
    resolved = Path(path).expanduser().resolve(strict=False)
    resolved_str = str(resolved)
    for prefix in prefixes or get_ingest_paths_allowed_prefixes():
        root = Path(prefix).expanduser().resolve(strict=False)
        root_str = str(root)
        if resolved_str == root_str or resolved_str.startswith(root_str + os.sep):
            return True
    return False


def validate_ingest_path_entry(path: str, *, index: int) -> None:
    """Raise ValueError with a user-facing message when a path is invalid."""
    stripped = path.strip()
    if not stripped:
        raise ValueError("Ingest path cannot be empty")
    if " " in stripped:
        raise ValueError(
            "Indexed folder path cannot contain spaces (Docker Compose limitation). "
            "Move or rename the folder to a path without spaces."
        )
    if ".." in Path(stripped).parts:
        raise ValueError("Ingest path cannot contain '..' components")
    if stripped in _FORBIDDEN_INGEST_PATHS:
        raise ValueError(f"Ingest path {stripped!r} is not allowed")

    if index == 0:
        container_path = host_ingest_path_to_container_index0(stripped)
        if not resolved_path_under_prefix(container_path):
            raise ValueError(
                f"Primary ingest path must resolve under allowed prefix(es) "
                f"{get_ingest_paths_allowed_prefixes()!r} inside the container "
                f"(got container path {container_path!r})"
            )
        return

    allowed = host_ingest_allowed_prefixes()
    if not resolved_path_under_prefix(stripped, allowed):
        if stripped.startswith("/host/") and not Path("/host").exists():
            raise ValueError(
                "Host browse mount not available. Copy docker-compose.override.yml.<os> "
                "to docker-compose.override.yml and restart the stack, then pick the folder again."
            )
        raise ValueError(
            f"Ingest path must be under allowed prefix(es) {allowed!r} (got {stripped!r})"
        )
    if not Path(stripped).is_dir():
        raise ValueError(
            f"Ingest path {stripped!r} is not an existing directory inside the orchestrator"
        )


def build_ingest_paths_env_lists(stored_paths: list[str]) -> tuple[list[str], list[str]]:
    """Return (host_paths_for_env, container_paths_for_env) from a stored ingest_paths list."""
    if not stored_paths:
        return [], []
    host_paths = list(stored_paths)
    container_paths = [host_ingest_path_to_container(i, p) for i, p in enumerate(stored_paths)]
    return host_paths, container_paths


def get_inbox_mode() -> InboxMode:
    """``LUMOGIS_INBOX_MODE``: ``event`` | ``poll`` | ``off``; invalid values → ``off``."""
    raw = os.environ.get("LUMOGIS_INBOX_MODE", "event").strip().lower()
    if raw in ("event", "poll", "off"):
        return raw  # type: ignore[return-value]
    _log.error("Invalid LUMOGIS_INBOX_MODE=%r — coercing to off", raw)
    return "off"


def get_inbox_stability_delay_ms() -> int:
    return _safe_int_env("LUMOGIS_INBOX_STABILITY_DELAY_MS", 1500, minimum=100)


def get_inbox_poll_interval_s() -> int:
    return _safe_int_env("LUMOGIS_INBOX_POLL_INTERVAL_S", 10, minimum=5)


def get_inbox_max_file_bytes() -> int:
    mb = _safe_int_env("LUMOGIS_INBOX_MAX_FILE_MB", 200, minimum=1)
    return mb * 1024 * 1024


def get_inbox_owner_user_id() -> str:
    return os.environ.get("INBOX_OWNER_USER_ID", "").strip()


def ensure_inbox_directory() -> bool:
    """Create inbox directory once at startup when mode ≠ ``off`` and owner is set."""
    path = get_inbox_path()
    try:
        path.mkdir(parents=True, exist_ok=True)
        return True
    except OSError as exc:
        _log.warning("Could not create inbox directory %s: %s", path, exc)
        return False


# ---------------------------------------------------------------------------
# LUM-124 — memory-as-hint (retrieved context framing + entity confidence)
# ---------------------------------------------------------------------------

_unknown_entity_halflife_types: set[str] = set()
_correction_placeholder_invalid_warned: bool = False


def get_memory_hint_enabled() -> bool:
    """When true, chat ack messages remind the model that retrieved excerpts are hints."""
    raw = os.environ.get("LUMOGIS_MEMORY_HINT_ENABLED")
    if raw is None or str(raw).strip() == "":
        return True
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def get_memory_hint_confidence_threshold() -> float:
    """Diagnostic threshold for future clause-level hedging (default 0.7)."""
    return _safe_float_env("LUMOGIS_MEMORY_HINT_CONFIDENCE_THRESHOLD", 0.7)


def get_memory_hint_stale_days() -> int:
    """Stale-day horizon for optional diagnostics (default 30)."""
    return _safe_int_env("LUMOGIS_MEMORY_HINT_STALE_DAYS", 30, minimum=1)


def get_entity_half_life_days(entity_type: object) -> int:
    """Half-life in days for exponential confidence decay by entity_type bucket."""
    if entity_type is None or not isinstance(entity_type, str) or not entity_type.strip():
        return _safe_int_env("LUMOGIS_ENTITY_HALFLIFE_CONCEPT_DAYS", 365, minimum=1)
    et_key = entity_type.strip().lower()
    if et_key == "person":
        return _safe_int_env("LUMOGIS_ENTITY_HALFLIFE_PERSON_DAYS", 90, minimum=1)
    if et_key == "project":
        return _safe_int_env("LUMOGIS_ENTITY_HALFLIFE_PROJECT_DAYS", 14, minimum=1)
    if et_key == "org":
        return _safe_int_env("LUMOGIS_ENTITY_HALFLIFE_ORG_DAYS", 180, minimum=1)
    if et_key == "concept":
        return _safe_int_env("LUMOGIS_ENTITY_HALFLIFE_CONCEPT_DAYS", 365, minimum=1)
    if et_key == "file":
        return _safe_int_env("LUMOGIS_ENTITY_HALFLIFE_FILE_DAYS", 30, minimum=1)
    if et_key not in _unknown_entity_halflife_types:
        _log.debug(
            "Unknown entity_type for half-life bucket: %r — using CONCEPT half-life",
            entity_type,
        )
        _unknown_entity_halflife_types.add(et_key)
    return _safe_int_env("LUMOGIS_ENTITY_HALFLIFE_CONCEPT_DAYS", 365, minimum=1)


def get_memory_correction_placeholder_enabled() -> bool:
    """Strict truthy for LUMOGIS_MEMORY_CORRECTION_PLACEHOLDER (1/true/yes/on only)."""
    global _correction_placeholder_invalid_warned
    raw = os.environ.get("LUMOGIS_MEMORY_CORRECTION_PLACEHOLDER")
    if raw is None or str(raw).strip() == "":
        return False
    s = str(raw).strip().lower()
    if s in ("1", "true", "yes", "on"):
        return True
    if s and not _correction_placeholder_invalid_warned:
        _log.warning(
            "Invalid LUMOGIS_MEMORY_CORRECTION_PLACEHOLDER=%r — treating as false",
            raw,
        )
        _correction_placeholder_invalid_warned = True
    return False


def get_context_max_edges() -> int:
    """Max RELATES_TO neighbours listed per entity in CONTEXT_BUILDING (default 5)."""
    return _safe_int_env("LUMOGIS_CONTEXT_MAX_EDGES", 5, minimum=1)


def get_graph_max_cooccurrence_pairs() -> int:
    """Maximum RELATES_TO edge writes per ingestion event (write amplification cap).

    DB key: graph_max_cooccurrence_pairs. Env fallback: GRAPH_MAX_COOCCURRENCE_PAIRS (100).
    """
    raw = _get_setting(
        "graph_max_cooccurrence_pairs",
        os.environ.get("GRAPH_MAX_COOCCURRENCE_PAIRS", "100"),
    )
    try:
        return int(raw)
    except (ValueError, TypeError):
        return 100


def get_graph_viz_max_nodes() -> int:
    """Hard node cap for visualization API responses.

    DB key: graph_viz_max_nodes. Env fallback: GRAPH_VIZ_MAX_NODES (150).
    """
    raw = _get_setting(
        "graph_viz_max_nodes",
        os.environ.get("GRAPH_VIZ_MAX_NODES", "150"),
    )
    try:
        return int(raw)
    except (ValueError, TypeError):
        return 150


def get_graph_viz_max_edges() -> int:
    """Hard edge cap for visualization API responses.

    DB key: graph_viz_max_edges. Env fallback: GRAPH_VIZ_MAX_EDGES (300).
    """
    raw = _get_setting(
        "graph_viz_max_edges",
        os.environ.get("GRAPH_VIZ_MAX_EDGES", "300"),
    )
    try:
        return int(raw)
    except (ValueError, TypeError):
        return 300


def get_decay_half_life_relates_to() -> int:
    """Days until a RELATES_TO edge weight halves (temporal decay).

    DB key: decay_half_life_relates_to. Env fallback: DECAY_HALF_LIFE_RELATES_TO (365).
    """
    raw = _get_setting(
        "decay_half_life_relates_to",
        os.environ.get("DECAY_HALF_LIFE_RELATES_TO", "365"),
    )
    try:
        return int(raw)
    except (ValueError, TypeError):
        return 365


def get_decay_half_life_mentions() -> int:
    """Days until a MENTIONS edge weight halves (temporal decay).

    DB key: decay_half_life_mentions. Env fallback: DECAY_HALF_LIFE_MENTIONS (180).
    """
    raw = _get_setting(
        "decay_half_life_mentions",
        os.environ.get("DECAY_HALF_LIFE_MENTIONS", "180"),
    )
    try:
        return int(raw)
    except (ValueError, TypeError):
        return 180


def get_decay_half_life_discussed_in() -> int:
    """Days until a DISCUSSED_IN edge weight halves (temporal decay).

    DB key: decay_half_life_discussed_in. Env fallback: DECAY_HALF_LIFE_DISCUSSED_IN (30).
    """
    raw = _get_setting(
        "decay_half_life_discussed_in",
        os.environ.get("DECAY_HALF_LIFE_DISCUSSED_IN", "30"),
    )
    try:
        return int(raw)
    except (ValueError, TypeError):
        return 30


def get_dedup_cron_hour_utc() -> int:
    """Hour (UTC, 0–23) when the weekly deduplication job runs on Sundays.

    DB key: dedup_cron_hour_utc. Env fallback: DEDUP_CRON_HOUR_UTC (2).
    """
    raw = _get_setting(
        "dedup_cron_hour_utc",
        os.environ.get("DEDUP_CRON_HOUR_UTC", "2"),
    )
    try:
        return int(raw)
    except (ValueError, TypeError):
        return 2


# ---------------------------------------------------------------------------
# Ecosystem plumbing — out-of-process capability services (Area 2)
# ---------------------------------------------------------------------------


def get_capability_service_urls() -> list[str]:
    """Parse CAPABILITY_SERVICE_URLS env var into a clean list of base URLs.

    Comma-separated. Whitespace is stripped, empty entries are dropped.
    Returns [] when the env var is unset or empty so callers can short-circuit.
    """
    raw = os.environ.get("CAPABILITY_SERVICE_URLS", "")
    return [u.strip() for u in raw.split(",") if u.strip()]


def get_capability_registry():
    """Return the shared CapabilityRegistry singleton.

    Discovery is driven from main.py lifespan (startup) and an APScheduler
    job (5-minute refresh). The registry itself does not initiate discovery.
    """
    if "capability_registry" not in _instances:
        from services.capability_registry import CapabilityRegistry

        _instances["capability_registry"] = CapabilityRegistry()
        _log.info("CapabilityRegistry created")
    return _instances["capability_registry"]


def get_tool_catalog_enabled() -> bool:
    """If true, :mod:`loop` may append OOP capability tool schemas and dispatch them.

    Default **true** when unset. Set ``LUMOGIS_TOOL_CATALOG_ENABLED=false`` (or ``0`` / ``no``)
    to opt out explicitly. Truthy tokens: ``1`` / ``true`` / ``yes``. Not cached so tests can
    toggle env in-process.
    """
    raw = os.environ.get("LUMOGIS_TOOL_CATALOG_ENABLED", "").strip().lower()
    if raw == "":
        return True
    return raw in ("1", "true", "yes")


# ---------------------------------------------------------------------------
# Speech-to-text (STT‑1 foundation — port + fake adapter; STT‑2 = faster-whisper)
# ---------------------------------------------------------------------------

_stt_warned_invalid_debug_once: bool = False

_SINGLE_LABEL_HOST = re.compile(r"^[A-Za-z0-9.-]+$")


def _stt_optional_nonnegative_int_env(var: str, default_str: str) -> int:
    """Positive integer; empty ⇒ *default_str*."""
    raw = os.environ.get(var)
    val = raw.strip() if isinstance(raw, str) else ""
    val_u = default_str if val == "" else val
    try:
        n = int(val_u)
    except (TypeError, ValueError):
        _log.error("Invalid %s=%r — must be a positive integer", var, raw)
        raise RuntimeError(f"Invalid {var}: positive integer required") from None
    if n <= 0:
        _log.error("Invalid %s=%r — must be > 0", var, raw)
        raise RuntimeError(f"Invalid {var}: must be > 0")
    return n


def get_stt_timeout_sec() -> int:
    """HTTP round-trip cap for STT adapters (sidecar multipart POST), after semaphore."""
    return _stt_optional_nonnegative_int_env("STT_TIMEOUT_SEC", "300")


def get_stt_sidecar_ping_timeout_sec() -> int:
    return _stt_optional_nonnegative_int_env("STT_SIDECAR_PING_TIMEOUT_SEC", "3")


def get_stt_model() -> str:
    raw = os.environ.get("STT_MODEL", "base").strip()
    return raw if raw else "base"


def get_stt_language() -> str | None:
    """Optional default transcription language hint when the HTTP caller omits ``language``."""
    raw = (os.environ.get("STT_LANGUAGE") or "").strip()
    return raw if raw else None


def _parse_bool_stt_allow_remote(raw: str | None, var: str) -> bool:
    if raw is None or raw.strip() == "":
        return False
    v = raw.strip().lower()
    if v in ("true", "1", "yes", "on"):
        return True
    if v in ("false", "0", "no", "off"):
        return False
    _log.error("Invalid %s=%r — must be true or false", var, raw)
    raise RuntimeError(f"Invalid {var}: expected true or false") from None


def get_stt_sidecar_allow_remote() -> bool:
    return _parse_bool_stt_allow_remote(
        os.environ.get("STT_SIDECAR_ALLOW_REMOTE"),
        "STT_SIDECAR_ALLOW_REMOTE",
    )


def _validate_stt_path_fragment(env_name: str, raw: str, default_when_empty: str) -> str:
    val = raw.strip() if isinstance(raw, str) else ""
    p = default_when_empty if val == "" else val
    if not p.startswith("/"):
        _log.error("Invalid %s=%r — path must start with /", env_name, raw)
        raise RuntimeError(f"Invalid {env_name}: path must start with /") from None
    if ".." in p or "?" in p or "#" in p:
        _log.error("Invalid %s=%r — forbidden path/query/fragment pattern", env_name, raw)
        raise RuntimeError(f"Invalid {env_name}: path shape not allowed") from None
    return p


def get_stt_sidecar_health_path() -> str:
    return _validate_stt_path_fragment(
        "STT_SIDECAR_HEALTH_PATH",
        os.environ.get("STT_SIDECAR_HEALTH_PATH", ""),
        "/health",
    )


def get_stt_sidecar_transcribe_path() -> str:
    return _validate_stt_path_fragment(
        "STT_SIDECAR_TRANSCRIBE_PATH",
        os.environ.get("STT_SIDECAR_TRANSCRIBE_PATH", ""),
        "/v1/audio/transcriptions",
    )


def _stt_sidecar_host_allowed(host: str, *, allow_remote: bool) -> bool:
    hn = host.strip().lower()
    if allow_remote:
        return True
    if hn == "localhost":
        return True
    try:
        addr = ipaddress.ip_address(host)
        # IPv4-mapped IPv6 (e.g. ::ffff:8.8.8.8) must be classified by the embedded
        # v4 address; otherwise public v4 reachable only via v6 literal bypasses the
        # allowlist when STT_SIDECAR_ALLOW_REMOTE is false.
        embedded = getattr(addr, "ipv4_mapped", None)
        if embedded is not None:
            addr = embedded
        if addr.is_loopback or addr.is_private or addr.is_link_local:
            return True
        _log.error(
            "Rejected STT_SIDECAR_URL host=%r — public IPs require STT_SIDECAR_ALLOW_REMOTE",
            host,
        )
        return False
    except ValueError:
        pass
    if "." not in hn and _SINGLE_LABEL_HOST.match(host):
        return True
    _log.error(
        "Rejected STT_SIDECAR_URL hostname=%r — multi-label DNS hosts require "
        "STT_SIDECAR_ALLOW_REMOTE",
        host,
    )
    return False


def normalize_stt_sidecar_base_url(raw: str) -> str:
    """Validate ``STT_SIDECAR_URL`` — base origin only; return URL with no trailing slash."""
    v = raw.strip()
    if not v:
        raise RuntimeError("STT_SIDECAR_URL is required when STT_BACKEND=whisper_sidecar")
    parsed = urlparse(v)
    if parsed.scheme not in ("http", "https"):
        _log.error("Invalid STT_SIDECAR_URL scheme in %r", raw)
        raise RuntimeError("STT_SIDECAR_URL must use http:// or https://") from None
    if parsed.username is not None or parsed.password is not None:
        _log.error("Invalid STT_SIDECAR_URL (credentials forbidden) — %r", raw)
        raise RuntimeError("STT_SIDECAR_URL must not include credentials") from None
    if parsed.params or parsed.query or parsed.fragment:
        _log.error("Invalid STT_SIDECAR_URL (query/fragment/params forbidden) — %r", raw)
        raise RuntimeError("STT_SIDECAR_URL must not contain query or fragment") from None
    hostport = parsed.netloc.strip()
    if not hostport:
        raise RuntimeError("STT_SIDECAR_URL must include host")
    hostname = parsed.hostname or ""
    if not hostname:
        raise RuntimeError("STT_SIDECAR_URL must include host")
    raw_path = parsed.path or ""
    if raw_path not in ("", "/"):
        _log.error("Invalid STT_SIDECAR_URL — base URL path must be empty (%r)", raw)
        raise RuntimeError(
            "STT_SIDECAR_URL must not contain a base path segment — use "
            "STT_SIDECAR_HEALTH_PATH and STT_SIDECAR_TRANSCRIBE_PATH",
        ) from None

    allowed = _stt_sidecar_host_allowed(hostname, allow_remote=get_stt_sidecar_allow_remote())
    if not allowed:
        raise RuntimeError(
            "STT_SIDECAR_URL host denied under STT_SIDECAR_ALLOW_REMOTE setting",
        )

    # Canonicalize: preserve scheme, hostname, netloc (with port).
    canon = urlunparse((parsed.scheme, parsed.netloc, "", "", "", ""))
    return canon.rstrip("/")


def get_stt_sidecar_api_key() -> str | None:
    v = os.environ.get("STT_SIDECAR_API_KEY", "").strip()
    return v if v else None


def get_stt_backend() -> Literal["none", "fake_stt", "whisper_sidecar"]:
    """Exact (case-sensitive) ``STT_BACKEND``. Raises ``RuntimeError`` when invalid/reserved."""

    raw = os.environ.get("STT_BACKEND")
    if raw is None or str(raw).strip() == "":
        return "none"
    val = str(raw).strip()
    if val == "none":
        return "none"
    if val == "fake_stt":
        return "fake_stt"
    if val == "whisper_sidecar":
        return "whisper_sidecar"
    if val == "faster_whisper":
        _log.error("STT_BACKEND=faster_whisper is not implemented in this build")
        raise RuntimeError("faster_whisper backend is not implemented in this build")
    _log.error(
        "Invalid STT_BACKEND=%r — must be one of none, fake_stt, whisper_sidecar, faster_whisper",
        val,
    )
    raise RuntimeError(f"Invalid STT_BACKEND: {val!r}")


def _stt_positive_int(var: str, default: str) -> int:
    raw = os.environ.get(var)
    val = raw.strip() if isinstance(raw, str) else ""
    val = default if val == "" else val
    try:
        n = int(val)
    except (TypeError, ValueError):
        _log.error("Invalid %s=%r — must be a positive integer", var, raw)
        raise RuntimeError(f"Invalid {var}: positive integer required") from None
    if n <= 0:
        _log.error("Invalid %s=%r — must be > 0", var, raw)
        raise RuntimeError(f"Invalid {var}: must be > 0")
    return n


def get_stt_max_audio_bytes() -> int:
    return _stt_positive_int("STT_MAX_AUDIO_BYTES", "26214400")


def get_stt_max_duration_sec() -> int:
    return _stt_positive_int("STT_MAX_DURATION_SEC", "600")


def parse_stt_debug_log_transcript() -> bool:
    """Strict bool for transcript debug logging — invalid ⇒ False + WARNING once."""
    global _stt_warned_invalid_debug_once
    raw = (os.environ.get("STT_DEBUG_LOG_TRANSCRIPT") or "false").strip().lower()
    if raw in ("", "false", "0", "no"):
        return False
    if raw in ("true", "1", "yes"):
        return True
    if not _stt_warned_invalid_debug_once:
        _log.warning(
            "Invalid STT_DEBUG_LOG_TRANSCRIPT=%r — defaulting to false",
            os.environ.get("STT_DEBUG_LOG_TRANSCRIPT"),
        )
        _stt_warned_invalid_debug_once = True
    return False


def get_speech_to_text():
    """Return wired STT adapter or ``None`` when ``STT_BACKEND=none``.

    Lazy-loads adapters only for active backends — ``services`` callers use this
    accessor and never import ``adapters`` directly.
    """
    bk = get_stt_backend()
    if bk == "none":
        return None

    key = f"speech_to_text:{bk}"
    if key in _instances:
        return _instances[key]  # type: ignore[no-any-return]

    if bk == "fake_stt":
        from adapters.fake_stt import FakeSpeechToTextAdapter

        _instances[key] = FakeSpeechToTextAdapter()
        _log.info("SpeechToText adapter resolved: FakeSpeechToTextAdapter (fake_stt)")
        return _instances[key]  # type: ignore[no-any-return]

    if bk == "whisper_sidecar":
        from adapters.whisper_sidecar_stt import WhisperSidecarSpeechToTextAdapter

        raw_url = os.environ.get("STT_SIDECAR_URL", "")
        base_url = normalize_stt_sidecar_base_url(raw_url)
        _instances[key] = WhisperSidecarSpeechToTextAdapter(
            base_url=base_url,
            health_path=get_stt_sidecar_health_path(),
            transcribe_path=get_stt_sidecar_transcribe_path(),
            http_timeout_sec=get_stt_timeout_sec(),
            ping_timeout_sec=get_stt_sidecar_ping_timeout_sec(),
            api_key=get_stt_sidecar_api_key(),
        )
        _log.info("SpeechToText adapter resolved: WhisperSidecarSpeechToTextAdapter")
        return _instances[key]  # type: ignore[no-any-return]

    raise RuntimeError(f"Internal STT wiring error for backend={bk!r}")


def get_capability_bearer_for_service(service_id: str) -> str | None:
    """Per-service POST bearer for out-of-process ``/tools/*`` calls (optional).

    Env key: ``LUMOGIS_CAPABILITY_BEARER_<UPPER_SANITIZED_ID>`` where
    non-alphanumerics are replaced with ``_`` (e.g. ``com.example.svc`` →
    ``LUMOGIS_CAPABILITY_BEARER_COM_EXAMPLE_SVC``). When unset, generic OOP
    tools are not added to the LLM list (fail-closed) unless a future
    manifest adds explicit auth.
    """
    if not service_id or not str(service_id).strip():
        return None
    safe = "".join(c if c.isalnum() else "_" for c in str(service_id)).upper()[:120]
    v = os.environ.get(f"LUMOGIS_CAPABILITY_BEARER_{safe}", "").strip()
    return v or None


# ---------------------------------------------------------------------------
# Graph mode — selects how the knowledge graph is wired into Core
# ---------------------------------------------------------------------------

_VALID_GRAPH_MODES = {"inprocess", "service", "disabled"}


@cache
def _cached_validated_graph_mode_from_env() -> str:
    """Read and validate GRAPH_MODE from the environment (no effective override)."""
    raw_display = os.environ.get("GRAPH_MODE", "disabled").strip()
    token = raw_display.lower()
    if token not in _VALID_GRAPH_MODES:
        _log.warning(
            "GRAPH_MODE=%r is not one of %s — falling back to %r",
            raw_display,
            sorted(_VALID_GRAPH_MODES),
            "disabled",
            extra={
                "event": "graph_mode_fallback",
                "requested_mode": raw_display,
                "effective_mode": "disabled",
                "reason": "invalid_graph_mode_env",
            },
        )
        return "disabled"
    return token


def read_graph_mode_from_env() -> str:
    """Return validated GRAPH_MODE from the environment for lifespan wiring.

    Ignores :func:`set_effective_graph_mode_for_process`; use before publishing
    the effective mode after :func:`main._wire_graph_mode_handlers`.
    """
    return _cached_validated_graph_mode_from_env()


def set_effective_graph_mode_for_process(mode: str | None) -> None:
    """Publish Core's resolved graph mode so :func:`get_graph_mode` matches wiring.

    Call exactly once per process after graph handlers are wired. Pass ``None``
    in tests to drop the override; pair with :func:`clear_graph_mode_env_cache`
    when tests mutate ``GRAPH_MODE``.
    """
    global _effective_graph_mode
    prev = _effective_graph_mode
    _effective_graph_mode = mode
    if prev != mode:
        _cached_validated_graph_mode_from_env.cache_clear()


def clear_graph_mode_env_cache() -> None:
    """Clear the env-based GRAPH_MODE cache (test harness / env mutation)."""
    _cached_validated_graph_mode_from_env.cache_clear()


def get_graph_mode() -> str:
    """Return the effective GRAPH_MODE after lifespan publish.

    Defaults to ``disabled`` when ``GRAPH_MODE`` is unset. Unknown env values map
    to ``disabled`` with a structured WARNING. After startup, reflects the mode
    returned from :func:`main._wire_graph_mode_handlers` (may differ from raw
    env when premium service or in-process plugin pieces are absent).
    """
    if _effective_graph_mode is not None:
        return _effective_graph_mode
    return _cached_validated_graph_mode_from_env()


def get_kg_service_url() -> str:
    """Return the base URL of the external lumogis-graph service.

    Used by Core's `graph_webhook_dispatcher` and the `query_graph` proxy
    ToolSpec when `GRAPH_MODE=service`. Defaults to the in-cluster service
    name `http://lumogis-graph:8001` (matches the docker-compose.premium.yml
    overlay). Trailing slashes are stripped so callers can safely append paths.

    Not cached — operators may rotate the URL via .env + container restart;
    repeat env reads here cost nothing meaningful versus the HTTP call that
    follows.
    """
    raw = os.environ.get("KG_SERVICE_URL", "http://lumogis-graph:8001").strip()
    return raw.rstrip("/")


def get_kg_webhook_secret() -> str | None:
    """Return the bearer token shared between Core and the KG service, or None.

    `None` means "no shared secret configured". Behaviour:
      - Core: omits the `Authorization` header on outbound webhook/context calls.
      - KG:   accepts unauthenticated calls iff `KG_ALLOW_INSECURE_WEBHOOKS=true`,
              else returns 503 (see services/lumogis-graph/routes/webhook.py).

    Whitespace is stripped; empty strings collapse to None so an
    accidentally-blank `GRAPH_WEBHOOK_SECRET=` line in .env is treated the
    same as the variable being absent.

    The ``query_graph`` HTTP proxy path (:func:`get_graph_proxy_require_service_bearer`)
    applies a **stricter** gate: it fail-closes outbound calls without a
    configured secret unless the operator sets
    ``LUMOGIS_GRAPH_PROXY_ALLOW_INSECURE_MISSING_SECRET``. Other Core readers
    of this secret (e.g. graph webhook dispatcher) keep their historical
    contract unchanged.
    """
    raw = os.environ.get("GRAPH_WEBHOOK_SECRET", "").strip()
    return raw or None


def get_graph_proxy_require_service_bearer() -> bool:
    """Return whether Core requires ``GRAPH_WEBHOOK_SECRET`` before HTTP to KG ``query_graph``.

    Used by :func:`services.kg_premium_core.graph_query_tool_proxy_call`
    when ``GRAPH_MODE=service``. Implements the posture matrix::

        secret present (any legacy opt-in env) → True (send bearer when configured)
        no secret + no explicit Core opt-in → True (fail-closed: no TCP)
        no secret + Core opt-in ``1``/``true``/``yes`` → False (legacy POST parity)

    The opt-in env is evaluated **only** on Core — it does **not** mirror
    ``KG_ALLOW_INSECURE_WEBHOOKS`` from the KG service process.
    """
    secret_present = bool(get_kg_webhook_secret())
    raw = os.environ.get("LUMOGIS_GRAPH_PROXY_ALLOW_INSECURE_MISSING_SECRET", "")
    allow_legacy = raw.strip().lower() in ("1", "true", "yes")
    if secret_present:
        return True
    if allow_legacy:
        return False
    return True


def shutdown() -> None:
    """Close connections and release resources."""
    global _effective_graph_mode, _graph_store_import_warning_emitted

    store = _instances.get("metadata_store")
    if store and hasattr(store, "close"):
        store.close()  # type: ignore[union-attr]
    _instances.clear()
    _effective_graph_mode = None
    _graph_store_import_warning_emitted = False
    _cached_validated_graph_mode_from_env.cache_clear()
    _log.info("Config shutdown: all adapter instances released")


# ---------------------------------------------------------------------------
# Connector credentials change-listener wiring.
#
# Plan ``llm_provider_keys_per_user_migration`` Pass 2.5: when a user
# PUTs/DELETEs an ``llm_*`` row through any path (user-facing route,
# admin-on-behalf route, or the migration script), the substrate fires
# this listener so we can drop that user's cached cloud adapters.
#
# Import-cycle safety: ``services.connector_credentials`` does
# ``import config`` at top level but accesses ``config.<x>`` only
# inside function bodies, so the partial-module state at this point
# is irrelevant — the listener registration just appends a callable
# to the substrate's ``_LISTENERS`` list, which is fully initialised
# by the time control returns here.
# ---------------------------------------------------------------------------


def _on_connector_credential_change(*, user_id: str, connector: str, action: str) -> None:
    """Funnel substrate change events into the per-user LLM cache invalidator."""
    if connector.startswith("llm_"):
        invalidate_llm_cache_for_user(user_id)


def _reregister_listeners_for_tests() -> None:
    """Re-attach the change listener after a substrate reset.

    Tests that call ``services.connector_credentials.reset_listeners_for_tests()``
    use this to rewire the production listener without having to know
    the listener function's name.
    """
    from services import connector_credentials as _ccs

    _ccs.register_change_listener(_on_connector_credential_change)


def _register_credential_listeners() -> None:
    """Idempotent registration of the per-user LLM cache listener.

    Calling this twice would fire the listener twice for every change,
    so we guard with a module-level flag.
    """
    global _LISTENERS_REGISTERED
    if _LISTENERS_REGISTERED:
        return
    _reregister_listeners_for_tests()
    _LISTENERS_REGISTERED = True


_LISTENERS_REGISTERED: bool = False
_register_credential_listeners()
