"""Shared test fixtures: mock adapters, test config, test client."""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

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
        self, collection: str, vector: list[float], limit: int, threshold: float
    ) -> list[dict]:
        return self._collections.get(collection, [])[:limit]

    def delete(self, collection: str, id: str) -> None:
        items = self._collections.get(collection, [])
        self._collections[collection] = [i for i in items if i["id"] != id]

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


@pytest.fixture
def mock_vector_store():
    return MockVectorStore()


@pytest.fixture
def mock_metadata_store():
    return MockMetadataStore()


@pytest.fixture
def mock_embedder():
    return MockEmbedder()


@pytest.fixture(autouse=True)
def _override_config(mock_vector_store, mock_metadata_store, mock_embedder):
    """Replace config singletons with mocks for every test."""
    _config._instances["vector_store"] = mock_vector_store
    _config._instances["metadata_store"] = mock_metadata_store
    _config._instances["embedder"] = mock_embedder
    yield
    _config._instances.clear()
