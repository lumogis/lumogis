# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Lumogis
"""
Wiring layer: reads .env, returns cached adapter singletons.

Every get_*() call returns the same object after first construction.
Call shutdown() during app teardown to close connections.
"""

import logging
import os
from pathlib import Path

import yaml
from ports.embedder import Embedder
from ports.llm_provider import LLMProvider
from ports.metadata_store import MetadataStore
from ports.vector_store import VectorStore

_log = logging.getLogger(__name__)
_instances: dict[str, object] = {}
_models_config: dict | None = None


def _load_models_yaml() -> dict:
    """Load and cache config/models.yaml."""
    global _models_config
    if _models_config is None:
        config_path = os.environ.get(
            "MODELS_CONFIG",
            str(Path(__file__).resolve().parent / "models.yaml"),
        )
        with open(config_path) as f:
            _models_config = yaml.safe_load(f).get("models", {})
    return _models_config


def get_model_config(model_name: str) -> dict:
    """Return a single model's config entry from models.yaml."""
    models = _load_models_yaml()
    if model_name not in models:
        raise ValueError(f"Unknown model '{model_name}'. Available: {list(models.keys())}")
    return models[model_name]


def is_local_model(model_name: str) -> bool:
    """True if the model runs locally (e.g. via Ollama); used for loading hints."""
    try:
        cfg = get_model_config(model_name)
        base = (cfg.get("base_url") or "").lower()
        return "ollama" in base
    except ValueError:
        return False


def get_llm_provider(model_name: str) -> LLMProvider:
    """Return a cached LLMProvider adapter for the given model name."""
    key = f"llm_{model_name}"
    if key not in _instances:
        cfg = get_model_config(model_name)
        proxy = cfg.get("proxy_url")
        adapter_type = cfg["adapter"]

        if adapter_type == "anthropic":
            from adapters.anthropic_llm import AnthropicLLM

            api_key = os.environ.get(cfg.get("api_key_env", ""), "")
            _instances[key] = AnthropicLLM(
                model=cfg["model"],
                api_key=api_key,
                base_url=proxy,
            )
        elif adapter_type == "openai":
            from adapters.openai_llm import OpenAILLM

            _instances[key] = OpenAILLM(
                model=cfg["model"],
                base_url=proxy or cfg.get("base_url"),
                api_key=os.environ.get(cfg.get("api_key_env", ""), None),
                context_budget=cfg.get("context_budget"),
            )
        else:
            raise ValueError(f"Unknown adapter type '{adapter_type}' for model '{model_name}'")

        _log.info(
            "LLM provider created: %s (adapter=%s, model=%s)",
            model_name,
            adapter_type,
            cfg["model"],
        )
    return _instances[key]  # type: ignore[return-value]


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
        backend = os.environ.get("RERANKER_BACKEND", "bge")
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


def shutdown() -> None:
    """Close connections and release resources."""
    store = _instances.get("metadata_store")
    if store and hasattr(store, "close"):
        store.close()  # type: ignore[union-attr]
    _instances.clear()
    _log.info("Config shutdown: all adapter instances released")
