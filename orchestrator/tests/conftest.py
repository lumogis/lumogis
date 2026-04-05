# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Lumogis
"""Shared test fixtures: mock adapters, test config, test client."""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Point config loaders to the actual files in config/ (project root).
# Without this, the defaults resolve to orchestrator/config/ which doesn't exist —
# files live at repo-root/config/ and are mounted into Docker at /app/config/.
_CONFIG_DIR = Path(__file__).parent.parent.parent / "config"
os.environ.setdefault("MODELS_CONFIG", str(_CONFIG_DIR / "models.yaml"))
os.environ.setdefault("OLLAMA_CATALOG_FALLBACK", str(_CONFIG_DIR / "ollama_catalog_fallback.json"))

import config as _config


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
            for clause in filter.get("must", []):
                key = clause["key"]
                val = clause["match"]["value"]
                items = [i for i in items if i.get("payload", {}).get(key) == val]
        return [{"id": i["id"], "score": 1.0, "payload": i["payload"]} for i in items[:limit]]

    def delete(self, collection: str, id: str) -> None:
        items = self._collections.get(collection, [])
        self._collections[collection] = [i for i in items if i["id"] != id]

    def delete_where(self, collection: str, filter: dict) -> None:
        items = self._collections.get(collection, [])
        for clause in filter.get("must", []):
            key = clause["key"]
            val = clause["match"]["value"]
            items = [i for i in items if i.get("payload", {}).get(key) != val]
        self._collections[collection] = items

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
    _config._instances["vector_store"] = mock_vector_store
    _config._instances["metadata_store"] = mock_metadata_store
    _config._instances["embedder"] = mock_embedder
    _config._instances["reranker"] = None
    _config._instances["scheduler"] = mock_scheduler
    yield
    _config._instances.clear()


@pytest.fixture(autouse=True)
def _mock_watcher(monkeypatch):
    """Prevent the filesystem watcher from starting during tests.

    start_watcher uses watchdog to monitor a real path; that path does not
    exist in the local test environment (it lives inside Docker).
    """
    monkeypatch.setattr("services.ingest.start_watcher", lambda path: None)
    monkeypatch.setattr("services.ingest.stop_watcher", lambda: None)
