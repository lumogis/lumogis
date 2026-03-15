"""
Wiring layer: reads .env, returns cached adapter singletons.

Every get_*() call returns the same object after first construction.
Call shutdown() during app teardown to close connections.
"""

import logging
import os

from ports.embedder import Embedder
from ports.metadata_store import MetadataStore
from ports.vector_store import VectorStore

_log = logging.getLogger(__name__)
_instances: dict[str, object] = {}


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

_optional_adapters = {"ocr_extractor", "docx_extractor"}


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
            if name in _optional_adapters and not ocr_enabled:
                continue
            try:
                importlib.import_module(f"adapters.{name}")
            except ImportError:
                _log.debug("Skipping adapter %s (missing deps)", name)
        _instances["extractors"] = dict(_extractor_registry)
    return _instances["extractors"]


def shutdown() -> None:
    """Close connections and release resources."""
    store = _instances.get("metadata_store")
    if store and hasattr(store, "close"):
        store.close()  # type: ignore[union-attr]
    _instances.clear()
    _log.info("Config shutdown: all adapter instances released")
