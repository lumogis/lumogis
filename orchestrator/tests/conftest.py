# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Shared test fixtures: mock adapters, test config, test client."""

import os
import sys
import types
from contextlib import contextmanager
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Structured logging defaults for the test process (chunk:
# structured_audit_logging — plan D10). Set BEFORE any orchestrator
# import so that ``main.py``'s top-of-module ``configure_logging()``
# call (if pulled in transitively by a test) sees a known-good
# configuration. ``setdefault`` so a CI run that wants to assert JSON
# output can still override.
os.environ.setdefault("LOG_FORMAT", "console")
os.environ.setdefault("LOG_LEVEL", "DEBUG")

# Stub out apscheduler so that plugins/graph/__init__.py can be imported
# in unit tests without the full production dependency being installed.
# The scheduler itself is replaced by MockScheduler in the _override_config fixture.
if "apscheduler" not in sys.modules:
    _aps_pkg = types.ModuleType("apscheduler")
    _aps_schedulers = types.ModuleType("apscheduler.schedulers")
    _aps_bg = types.ModuleType("apscheduler.schedulers.background")

    class _MockBGScheduler:
        running = False
        def __init__(self, **kwargs): pass
        def start(self): self.running = True
        def shutdown(self, wait=True): self.running = False
        def add_job(self, *a, **kw): return None
        def get_job(self, job_id): return None
        def get_jobs(self): return []

    _aps_bg.BackgroundScheduler = _MockBGScheduler
    sys.modules["apscheduler"] = _aps_pkg
    sys.modules["apscheduler.schedulers"] = _aps_schedulers
    sys.modules["apscheduler.schedulers.background"] = _aps_bg

# Point config loaders to the actual files in config/ (project root).
# Two layouts to support:
#   * host checkout: __file__ = .../orchestrator/tests/conftest.py → repo/config
#   * Docker image:  __file__ = /app/tests/conftest.py and config/ is mounted
#                    at /opt/lumogis/config/ (see orchestrator/Dockerfile).
# We pick the first candidate that actually exists; if none do, we fall back
# to the host-checkout path so a missing-fixture failure surfaces with a
# meaningful path in the error message rather than silently returning [].
_CONFIG_CANDIDATES = [
    Path(__file__).parent.parent.parent / "config",
    Path("/opt/lumogis/config"),
]
_CONFIG_DIR = next(
    (p for p in _CONFIG_CANDIDATES if (p / "ollama_catalog_fallback.json").is_file()),
    _CONFIG_CANDIDATES[0],
)
os.environ.setdefault("MODELS_CONFIG", str(_CONFIG_DIR / "models.yaml"))
os.environ.setdefault("OLLAMA_CATALOG_FALLBACK", str(_CONFIG_DIR / "ollama_catalog_fallback.json"))

# Unit tests assume the legacy in-process graph plugin layout and an empty
# capability registry by default. When the test container inherits the host
# stack's runtime env (`GRAPH_MODE=service`, `CAPABILITY_SERVICE_URLS=...`),
# `plugins/graph/__init__.py` short-circuits to `router = None` (no
# `/graph/backfill` route) and the lifespan auto-registers the live
# `lumogis-graph` service in the registry — both break tests that expect a
# clean default. Unset them here at module-load time, BEFORE any test module
# imports `plugins.graph.*` or `services.capability_registry`. Tests that
# specifically need a non-default value set it via `monkeypatch.setenv`.
for _stack_only_env in ("GRAPH_MODE", "CAPABILITY_SERVICE_URLS"):
    os.environ.pop(_stack_only_env, None)

import config as _config


def _match_clause(payload: dict, clause: dict) -> bool:
    """Evaluate a single Qdrant filter clause against a payload dict.

    Supports the small subset Lumogis actually uses today:
      * ``{"key": k, "match": {"value": v}}`` — equality
      * ``{"key": k, "match": {"any": [...]}}`` — membership
      * Nested ``{"must": [...]}`` / ``{"should": [...]}`` blocks

    The real Qdrant filter language is much richer; mirroring just what
    ``visibility.visible_qdrant_filter`` and the per-user/scope routes
    actually emit keeps the mock honest without re-implementing Qdrant.
    """
    if "must" in clause or "should" in clause:
        return _matches_qdrant_filter(payload, clause)
    key = clause["key"]
    match = clause["match"]
    actual = payload.get(key)
    if "value" in match:
        return actual == match["value"]
    if "any" in match:
        return actual in match["any"]
    raise NotImplementedError(f"MockVectorStore: unsupported match shape {match!r}")


def _matches_qdrant_filter(payload: dict, flt: dict) -> bool:
    """Top-level filter eval: AND across ``must``, OR across ``should``."""
    if "must" in flt:
        if not all(_match_clause(payload, c) for c in flt["must"]):
            return False
    if "should" in flt:
        if not any(_match_clause(payload, c) for c in flt["should"]):
            return False
    return True


class MockVectorStore:
    def __init__(self):
        self._collections: dict[str, list] = {}

    def ping(self) -> bool:
        return True

    def create_collection(self, name: str, vector_size: int) -> None:
        self._collections[name] = []

    def upsert(self, collection: str, id: str, vector: list[float], payload: dict) -> None:
        self._collections.setdefault(collection, []).append(
            {"id": id, "vector": vector, "payload": payload}
        )

    def search(
        self,
        collection: str,
        vector: list[float],
        limit: int,
        threshold: float,
        filter: dict | None = None,
        sparse_query: str | None = None,
    ) -> list[dict]:
        items = self._collections.get(collection, [])
        if filter:
            items = [
                i for i in items
                if _matches_qdrant_filter(i.get("payload", {}), filter)
            ]
        return [{"id": i["id"], "score": 1.0, "payload": i["payload"]} for i in items[:limit]]

    def delete(self, collection: str, id: str) -> None:
        items = self._collections.get(collection, [])
        self._collections[collection] = [i for i in items if i["id"] != id]

    def delete_where(self, collection: str, filter: dict) -> None:
        items = self._collections.get(collection, [])
        self._collections[collection] = [
            i for i in items
            if not _matches_qdrant_filter(i.get("payload", {}), filter)
        ]

    def count(self, collection: str) -> int:
        return len(self._collections.get(collection, []))


class MockMetadataStore:
    def __init__(self):
        self._data: list[dict] = []

    def ping(self) -> bool:
        return True

    def execute(self, query: str, params: tuple | None = None) -> None:
        pass

    def fetch_one(self, query: str, params: tuple | None = None) -> dict | None:
        return None

    def fetch_all(self, query: str, params: tuple | None = None) -> list[dict]:
        return []

    def close(self) -> None:
        pass

    @contextmanager
    def transaction(self):
        yield


class MockEmbedder:
    def ping(self) -> bool:
        return True

    @property
    def vector_size(self) -> int:
        return 768

    def embed(self, text: str) -> list[float]:
        return [0.0] * 768

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] * 768 for _ in texts]


class MockScheduler:
    """No-op APScheduler stand-in for unit tests.

    Supports the full scheduler API used by main.py and signal monitors:
    start(), shutdown(), running, add_job(), get_job(), get_jobs().
    """

    running: bool = False

    def start(self) -> None:
        self.running = True

    def shutdown(self, wait: bool = True) -> None:
        self.running = False

    def add_job(self, *args, **kwargs):
        return None

    def get_job(self, job_id: str):
        return None

    def get_jobs(self) -> list:
        return []


@pytest.fixture
def mock_vector_store():
    return MockVectorStore()


@pytest.fixture
def mock_metadata_store():
    return MockMetadataStore()


@pytest.fixture
def mock_embedder():
    return MockEmbedder()


@pytest.fixture
def mock_scheduler():
    return MockScheduler()


@pytest.fixture(autouse=True)
def _override_config(mock_vector_store, mock_metadata_store, mock_embedder, mock_scheduler, monkeypatch):
    """Replace config singletons with mocks for every test.

    Also sets RERANKER_BACKEND=none so the lifespan never tries to import
    sentence_transformers (which is only available inside the Docker image).
    APScheduler is mocked via MockScheduler so the local venv does not need
    the apscheduler package — it is only available inside the Docker image.
    """
    monkeypatch.setenv("RERANKER_BACKEND", "none")
    # Test defaults: tests assume the legacy in-process graph plugin and an
    # empty capability registry. The test container inherits the live stack's
    # env (`GRAPH_MODE=service`, `CAPABILITY_SERVICE_URLS=...`) which would
    # otherwise (a) self-disable `plugins/graph/` (no `/graph/backfill`
    # route) and (b) trigger lifespan discovery of the live `lumogis-graph`
    # service, polluting registry-counting assertions. Tests that specifically
    # need a non-default value set it via `monkeypatch.setenv` after this
    # autouse fixture runs.
    monkeypatch.delenv("GRAPH_MODE", raising=False)
    monkeypatch.delenv("CAPABILITY_SERVICE_URLS", raising=False)
    # Bypass main._enforce_auth_consistency for tests that exercise
    # AUTH_ENABLED=true without seeding a real admin row. The variable
    # name is intentionally long and ugly so it cannot accidentally
    # appear in production code. Phase 1 / Phase 2 tests that *want*
    # the gate flip it back to "false" via the `no_skip_consistency`
    # fixture in tests/test_auth_phase1.py.
    monkeypatch.setenv(
        "_LUMOGIS_TEST_SKIP_AUTH_CONSISTENCY_DO_NOT_SET_IN_PRODUCTION",
        "true",
    )
    _config._instances["vector_store"] = mock_vector_store
    _config._instances["metadata_store"] = mock_metadata_store
    _config._instances["embedder"] = mock_embedder
    _config._instances["reranker"] = None
    _config._instances["scheduler"] = mock_scheduler
    # `get_graph_mode` is `@cache`-decorated; clear before AND after every test
    # so env-var mutations via `monkeypatch.setenv("GRAPH_MODE", ...)` actually
    # take effect and don't leak between tests.
    _config.get_graph_mode.cache_clear()
    yield
    _config._instances.clear()
    _config.get_graph_mode.cache_clear()


@pytest.fixture(autouse=True)
def _logging_reset():
    """Reset structlog + stdlib logging to a known baseline per test.

    Plan ``structured_audit_logging`` D10 — explicit pytest fixture
    instead of production-code branches that detect pytest. Tests that
    capture structured output via ``structlog.testing.capture_logs()``
    or that bind their own contextvars get a clean slate; tests that
    rely on ``caplog`` see the stdlib bridge re-attached fresh on every
    function.
    """
    from logging_config import reset_for_tests
    reset_for_tests()
    yield
    reset_for_tests()


@pytest.fixture(autouse=True)
def _mock_watcher(monkeypatch):
    """Prevent the filesystem watcher from starting during tests.

    start_watcher uses watchdog to monitor a real path; that path does not
    exist in the local test environment (it lives inside Docker).
    """
    monkeypatch.setattr("services.ingest.start_watcher", lambda path: None)
    monkeypatch.setattr("services.ingest.stop_watcher", lambda: None)
