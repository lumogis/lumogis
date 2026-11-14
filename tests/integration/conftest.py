"""Integration tests: require running stack (docker compose up -d).

Set LUMOGIS_API_URL if not http://127.0.0.1:8000.

When the stack runs with ``AUTH_ENABLED=true``, the ``api`` fixture logs in with
``LUMOGIS_WEB_SMOKE_EMAIL`` / ``LUMOGIS_WEB_SMOKE_PASSWORD`` (LUM-929).
Host-side tests reach graph routes on the lumogis-graph service
(``LUMOGIS_GRAPH_BASE_URL``), never on Core.
"""

import os
from pathlib import Path

import httpx
import pytest
from _reachability import skip_unless_reachable
from _smoke_auth import SmokeBearerAuth, open_api_client, workspace_inbox_dir

BASE_URL = os.environ.get("LUMOGIS_API_URL", "http://127.0.0.1:8000")
GRAPH_BASE_URL = os.environ.get("LUMOGIS_GRAPH_BASE_URL", "http://127.0.0.1:18001").rstrip("/")
REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="session")
def api():
    client, skip_reason = open_api_client(BASE_URL)
    if client is None:
        pytest.skip(skip_reason)
    yield client
    client.close()


@pytest.fixture(scope="session")
def kg_api(api):
    """Client for the lumogis-graph service, authenticated like ``api``."""
    skip_unless_reachable(GRAPH_BASE_URL + "/health", what="lumogis-graph")
    auth = api._auth if isinstance(api._auth, SmokeBearerAuth) else None
    client = httpx.Client(base_url=GRAPH_BASE_URL, timeout=180.0, auth=auth)
    yield client
    client.close()


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture(scope="session")
def inbox_dir() -> Path:
    return workspace_inbox_dir(REPO_ROOT)
