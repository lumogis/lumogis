"""Integration tests: require running stack (docker compose up -d).

Set LUMOGIS_API_URL if not http://127.0.0.1:8000.
"""

import os
from pathlib import Path

import httpx
import pytest

BASE_URL = os.environ.get("LUMOGIS_API_URL", "http://127.0.0.1:8000")
REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="session")
def api():
    client = httpx.Client(base_url=BASE_URL, timeout=180.0)
    try:
        r = client.get("/")
        if r.status_code != 200:
            pytest.skip(f"Orchestrator not healthy at {BASE_URL}: HTTP {r.status_code}")
    except httpx.ConnectError as e:
        pytest.skip(f"Orchestrator unreachable at {BASE_URL}: {e}")
    yield client
    client.close()


@pytest.fixture
def repo_root() -> Path:
    return REPO_ROOT
