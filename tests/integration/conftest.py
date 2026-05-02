"""Integration tests: require running stack (docker compose up -d).

Set LUMOGIS_API_URL if not http://127.0.0.1:8000.

When ``AUTH_ENABLED=true`` (RC compose overlay), host pytest mints a Bearer via
``POST /api/v1/auth/login`` using ``LUMOGIS_WEB_SMOKE_EMAIL`` /
``LUMOGIS_WEB_SMOKE_PASSWORD`` (see ``config/test.env.example``). Override those
in the environment when needed.
"""

import os
from pathlib import Path

import httpx
import pytest

BASE_URL = os.environ.get("LUMOGIS_API_URL", "http://127.0.0.1:8000")
REPO_ROOT = Path(__file__).resolve().parents[2]


def _login_headers(client: httpx.Client) -> dict[str, str]:
    email = os.environ.get("LUMOGIS_WEB_SMOKE_EMAIL", "").strip()
    password = os.environ.get("LUMOGIS_WEB_SMOKE_PASSWORD", "")
    if not email or len(password) < 12:
        return {}
    r = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    if r.status_code == 503:
        # AUTH_ENABLED=false single-user dev mode — middleware synthesises default user.
        return {}
    if r.status_code != 200:
        raise RuntimeError(
            f"integration login failed ({r.status_code}) for {email!r}: {r.text[:800]}",
        )
    token = r.json().get("access_token")
    if not token:
        raise RuntimeError("integration login: missing access_token in response")
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(scope="session")
def api():
    client = httpx.Client(base_url=BASE_URL, timeout=180.0)
    try:
        r = client.get("/healthz")
        if r.status_code != 200:
            pytest.skip(f"Orchestrator not healthy at {BASE_URL}: HTTP {r.status_code}")
    except httpx.ConnectError as e:
        pytest.skip(f"Orchestrator unreachable at {BASE_URL}: {e}")

    client.headers.update(_login_headers(client))

    yield client
    client.close()


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return REPO_ROOT
