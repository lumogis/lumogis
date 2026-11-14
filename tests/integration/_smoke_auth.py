# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Shared smoke-credential login and Bearer handling for repo-root live tests.

One place for the ``LUMOGIS_WEB_SMOKE_EMAIL`` / ``_PASSWORD`` login that the
live ``tests/integration`` files need when the stack runs with
``AUTH_ENABLED=true`` (LUM-929).

Two modules log in inline on purpose and are NOT converged here:
``test_public_rc_auth_session.py`` and ``test_lumogis_web_smoke.py`` exercise
the login endpoint itself, so a shared helper would hide what they test.

Nothing here prints the password or the token; assertion messages carry only
the first 800 characters of a response body.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from collections.abc import Generator
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import httpx

_MIN_PASSWORD_LEN = 12


def smoke_credentials() -> tuple[str, str] | None:
    """Return ``(email, password)`` when both are set and the password is long enough."""
    email = os.environ.get("LUMOGIS_WEB_SMOKE_EMAIL", "").strip()
    password = os.environ.get("LUMOGIS_WEB_SMOKE_PASSWORD", "")
    if not email or len(password) < _MIN_PASSWORD_LEN:
        return None
    return email, password


def public_origin() -> str:
    """``Origin`` header value for login (``LUMOGIS_PUBLIC_ORIGIN``, no trailing slash)."""
    raw = os.environ.get("LUMOGIS_PUBLIC_ORIGIN", "http://127.0.0.1").strip().rstrip("/")
    return raw or "http://127.0.0.1"


def login_access_token(
    base_url: str, *, transport: httpx.BaseTransport | None = None
) -> str | None:
    """POST ``/api/v1/auth/login`` with the smoke credentials.

    Returns the access token, or ``None`` on 503 (``AUTH_ENABLED=false``).
    Raises ``RuntimeError`` without credentials and ``AssertionError`` on any
    other non-200 response or a missing ``access_token``.
    """
    creds = smoke_credentials()
    if creds is None:
        raise RuntimeError(
            "LUMOGIS_WEB_SMOKE_EMAIL / LUMOGIS_WEB_SMOKE_PASSWORD unset or too short"
        )
    email, password = creds
    with httpx.Client(base_url=base_url, timeout=180.0, transport=transport) as raw:
        lr = raw.post(
            "/api/v1/auth/login",
            json={"email": email, "password": password},
            headers={"Origin": public_origin()},
        )
    if lr.status_code == 503:
        return None
    assert lr.status_code == 200, lr.text[:800]
    token = lr.json().get("access_token")
    assert token, f"login must return access_token; body: {lr.text[:800]}"
    return token


class SmokeBearerAuth(httpx.Auth):
    """Bearer auth that logs in lazily and re-logs in once on a 401.

    The retried response is returned whatever its status, so a persistent 401
    reaches the caller instead of looping.
    """

    def __init__(self, base_url: str, *, transport: httpx.BaseTransport | None = None) -> None:
        self._base_url = base_url
        self._transport = transport
        self._token: str | None = None

    def _login(self) -> str:
        token = login_access_token(self._base_url, transport=self._transport)
        assert token, "smoke login unavailable (AUTH_ENABLED=false?)"
        self._token = token
        return token

    def auth_flow(self, request: httpx.Request) -> Generator[httpx.Request, httpx.Response, None]:
        token = self._token or self._login()
        request.headers["Authorization"] = f"Bearer {token}"
        response = yield request
        if response.status_code == 401:
            request.headers["Authorization"] = f"Bearer {self._login()}"
            yield request


def open_api_client(
    base_url: str, *, transport: httpx.BaseTransport | None = None
) -> tuple[httpx.Client | None, str | None]:
    """Build the client the ``api`` fixture yields, or return a skip reason.

    ``(client, None)`` on success; ``(None, reason)`` when the stack should be
    skipped. A failed login with credentials set raises ``AssertionError``.
    """
    client = httpx.Client(base_url=base_url, timeout=180.0, transport=transport)
    try:
        r = client.get("/")
    except httpx.ConnectError as exc:
        client.close()
        return None, f"Orchestrator unreachable at {base_url}: {exc}"
    if r.status_code == 200:
        return client, None
    if r.status_code != 401:
        client.close()
        return None, f"Orchestrator not healthy at {base_url}: HTTP {r.status_code}"
    client.close()
    if smoke_credentials() is None:
        return None, "auth is on and LUMOGIS_WEB_SMOKE_EMAIL/PASSWORD are unset"
    client = httpx.Client(
        base_url=base_url,
        timeout=180.0,
        transport=transport,
        auth=SmokeBearerAuth(base_url, transport=transport),
    )
    r2 = client.get("/")
    if r2.status_code != 200:
        client.close()
        raise AssertionError(
            f"authenticated GET / at {base_url} returned {r2.status_code}: {r2.text[:800]}"
        )
    return client, None


def workspace_inbox_dir(repo_root: Path, env: Mapping[str, str] | None = None) -> Path:
    """Host path of the inbox the watcher reads (``WORKSPACE_HOST_DIR``/inbox)."""
    source = os.environ if env is None else env
    base = Path(source.get("WORKSPACE_HOST_DIR") or "./ai-workspace")
    if not base.is_absolute():
        base = repo_root / base
    return base / "inbox"


def llm_wait_s(default: float) -> float:
    """LLM-dependent wait in seconds; ``LUMOGIS_INTEG_LLM_TIMEOUT_S`` overrides *default*."""
    return float(os.environ.get("LUMOGIS_INTEG_LLM_TIMEOUT_S", default))


_FILE_INDEX_SQL = (
    "SELECT graph_projected_at IS NOT NULL, user_id FROM file_index "
    "WHERE position(%s in file_path) > 0 ORDER BY updated_at DESC LIMIT 1"
)


def file_index_row(
    path_fragment: str, *, connect: Callable[..., Any] | None = None
) -> dict[str, Any] | None:
    """Read the newest ``file_index`` row whose path contains *path_fragment*.

    ``GET /export`` is 410 and no HTTP route carries ``graph_projected_at``, so
    the RC Postgres (host port 5433) is read directly with one parameterised
    ``SELECT``. ``position()`` is a literal substring match, so ``_`` in a token
    is not a wildcard. Returns ``None`` when no row matches, otherwise
    ``{"projected": bool, "user_id": str}``. Connection errors propagate.
    """
    if connect is None:
        import psycopg2  # lazy: only live lanes need it

        connect = psycopg2.connect
    conn = connect(
        host=os.environ.get("POSTGRES_HOST", "127.0.0.1"),
        port=int(os.environ.get("POSTGRES_PORT", "5433")),
        user=os.environ.get("POSTGRES_USER"),
        password=os.environ.get("POSTGRES_PASSWORD"),
        dbname=os.environ.get("POSTGRES_DB"),
        connect_timeout=5,
    )
    try:
        cur = conn.cursor()
        cur.execute(_FILE_INDEX_SQL, (path_fragment,))
        row = cur.fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    return {"projected": bool(row[0]), "user_id": str(row[1])}


def current_user_id(client: httpx.Client) -> str:
    """``GET /api/v1/auth/me`` ``id`` — the seeded inbox owner on the RC stack."""
    r = client.get("/api/v1/auth/me")
    assert r.status_code == 200, f"/api/v1/auth/me returned {r.status_code}: {r.text[:800]}"
    return str(r.json()["id"])
