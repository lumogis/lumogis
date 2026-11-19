# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""LUM-915 — the graph verifier checks the JWT ``tv`` claim like Core does.

The rules mirrored here live in Core's ``orchestrator/auth.py``
(``require_tv_claim``, ``resolve_current_token_version``,
``jwt_revocation_failure_reason``). A change there has one place to compare.

Deliberate differences from Core, pinned below:

* no cache: a bump applies on the next request;
* a missing ``users`` row rejects (Core treats it as version 0);
* a failed lookup raises ``TokenCheckUnavailable`` (HTTP 503).
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

SECRET = "kg-test-access-secret-0123456789abcdef"
_SERVICE = Path(__file__).resolve().parents[1]
# In the Docker test image the service sits at /app, so there is no repo root above it.
_REPO = _SERVICE.parents[1] if len(_SERVICE.parents) > 1 else None


@pytest.fixture(autouse=True)
def _auth_on(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTH_SECRET", SECRET)
    monkeypatch.delenv("LUMOGIS_REQUIRE_TV_CLAIM", raising=False)
    monkeypatch.delenv("GRAPH_ADMIN_TOKEN", raising=False)


def _token(sub="u1", role="user", typ="access", tv=0, **extra) -> str:
    now = int(time.time())
    payload = {"sub": sub, "role": role, "typ": typ, "iat": now, "exp": now + 300, **extra}
    if tv is not None:
        payload["tv"] = tv
    return jwt.encode(payload, SECRET, algorithm="HS256")


def _bearer(tok) -> dict:
    return {"Authorization": f"Bearer {tok}"}


def _tv_lookups(store) -> list:
    return [q for q, _ in store.fetched if "token_version" in q]


# ---------------------------------------------------------------------------
# Verifier unit tests
# ---------------------------------------------------------------------------


def test_verify_accepts_matching_tv(mock_metadata_store):
    import auth

    mock_metadata_store.token_versions = {"u1": 3}
    payload = auth._verify_token(_token(tv=3))
    assert payload is not None and payload["sub"] == "u1"


def test_verify_rejects_stale_tv(mock_metadata_store):
    import auth

    mock_metadata_store.token_versions = {"u1": 4}
    assert auth._verify_token(_token(tv=3)) is None


def test_verify_rejects_missing_tv_by_default(mock_metadata_store):
    import auth

    mock_metadata_store.token_versions = {"u1": 0}
    assert auth._verify_token(_token(tv=None)) is None


def test_verify_accepts_missing_tv_when_claim_not_required(mock_metadata_store, monkeypatch):
    import auth

    monkeypatch.setenv("LUMOGIS_REQUIRE_TV_CLAIM", "false")
    payload = auth._verify_token(_token(tv=None))
    assert payload is not None
    assert _tv_lookups(mock_metadata_store) == []


@pytest.mark.parametrize(
    ("tv_claim", "env", "row", "expected"),
    [
        (3, None, 3, "accept"),
        (3, None, 4, "reject"),
        (None, None, 0, "reject"),
        (None, "true", 0, "reject"),
        (None, "false", 0, "accept"),
        (None, "0", 0, "accept"),
        (None, "no", 0, "accept"),
        (3, "false", 3, "accept"),  # claim present + lenient env: still checked...
        (3, "false", 4, "reject"),  # ...and still rejected when stale
        ("abc", None, 0, "reject"),
        (0, None, "missing", "reject"),  # Core: accepts at tv 0 — deliberate difference
        (0, None, "raises", "unavailable"),
    ],
)
def test_tv_rule_table(mock_metadata_store, monkeypatch, tv_claim, env, row, expected):
    import auth

    if env is not None:
        monkeypatch.setenv("LUMOGIS_REQUIRE_TV_CLAIM", env)
    if row == "raises":

        def _boom(query, params=None):
            raise RuntimeError("postgres down")

        mock_metadata_store.fetch_one = _boom
    elif row != "missing":
        mock_metadata_store.token_versions = {"u1": row}

    token = _token(tv=tv_claim)
    if expected == "unavailable":
        with pytest.raises(auth.TokenCheckUnavailable):
            auth._verify_token(token)
    elif expected == "accept":
        assert auth._verify_token(token) is not None
    else:
        assert auth._verify_token(token) is None


def test_verify_rejects_non_int_tv(mock_metadata_store):
    import auth

    mock_metadata_store.token_versions = {"u1": 0}
    assert auth._verify_token(_token(tv="abc")) is None


def test_verify_rejects_missing_sub(mock_metadata_store, monkeypatch):
    import auth

    now = int(time.time())
    tok = jwt.encode(
        {"typ": "access", "tv": 0, "iat": now, "exp": now + 300}, SECRET, algorithm="HS256"
    )
    assert auth._verify_token(tok) is None
    assert _tv_lookups(mock_metadata_store) == []


def test_verify_rejects_missing_user_row(mock_metadata_store):
    import auth

    mock_metadata_store.token_versions = {}
    assert auth._verify_token(_token(tv=0)) is None


def test_verify_fails_closed_on_db_error(mock_metadata_store, caplog):
    import auth

    def _boom(query, params=None):
        raise RuntimeError("postgres down")

    mock_metadata_store.fetch_one = _boom
    token = _token()
    with caplog.at_level(logging.WARNING):
        with pytest.raises(auth.TokenCheckUnavailable):
            auth._verify_token(token)
    assert "token_version lookup failed" in caplog.text
    assert token not in caplog.text


def test_verify_lookup_is_parameterised(mock_metadata_store):
    import auth

    mock_metadata_store.token_versions = {"u1": 0}
    assert auth._verify_token(_token()) is not None
    assert mock_metadata_store.fetched == [
        ("SELECT token_version FROM users WHERE id = %s", ("u1",))
    ]


def test_auth_module_imports_jwt_at_top_level():
    import auth

    assert auth.jwt is jwt


# ---------------------------------------------------------------------------
# Runtime dependency
# ---------------------------------------------------------------------------


def _pyjwt_floor(path: Path) -> str | None:
    for line in path.read_text().splitlines():
        line = line.strip()
        if line.lower().startswith("pyjwt") and not line.startswith("#"):
            return line.replace(" ", "").lower()
    return None


def test_graph_requirements_ship_pyjwt():
    assert _pyjwt_floor(_SERVICE / "requirements.txt") == "pyjwt>=2.8.0"


def test_graph_pyjwt_floor_matches_core():
    core = _REPO / "orchestrator" / "requirements-core.txt" if _REPO else None
    if core is None or not core.exists():
        pytest.skip("orchestrator tree not in this image")
    assert _pyjwt_floor(_SERVICE / "requirements.txt") == _pyjwt_floor(core)


# ---------------------------------------------------------------------------
# Route-level behaviour (auth on)
# ---------------------------------------------------------------------------


def _stats_app() -> TestClient:
    import graph.viz_routes
    from auth import auth_middleware

    app = FastAPI()
    app.middleware("http")(auth_middleware)
    app.include_router(graph.viz_routes.router)
    return TestClient(app)


def _mgm_app() -> TestClient:
    from auth import auth_middleware
    from routes.mgm import router

    app = FastAPI()
    app.middleware("http")(auth_middleware)
    app.include_router(router)
    return TestClient(app)


def test_valid_bearer_reaches_graph_read_route(mock_metadata_store):
    mock_metadata_store.token_versions = {"u1": 0}
    client = _stats_app()
    ok = client.get("/graph/stats", headers=_bearer(_token(role="user")))
    assert ok.status_code == 200
    assert "node_count" in ok.json()

    mock_metadata_store.token_versions = {"u1": 1}  # negative control
    assert client.get("/graph/stats", headers=_bearer(_token(role="user"))).status_code == 401


def test_stale_tv_gets_401_on_mgm(mock_metadata_store):
    client = _mgm_app()
    tok = _token(sub="admin-1", role="admin")
    mock_metadata_store.token_versions = {"admin-1": 1}
    assert client.get("/mgm", headers=_bearer(tok)).status_code == 401
    mock_metadata_store.token_versions = {"admin-1": 0}
    assert client.get("/mgm", headers=_bearer(tok)).status_code == 200


def test_demoted_admin_loses_mgm_immediately(mock_metadata_store):
    client = _mgm_app()
    tok = _token(sub="admin-1", role="admin")
    mock_metadata_store.token_versions = {"admin-1": 0}
    assert client.get("/mgm", headers=_bearer(tok)).status_code == 200
    mock_metadata_store.token_versions["admin-1"] = 1  # Core's bump on demotion
    assert client.get("/mgm", headers=_bearer(tok)).status_code == 401


def test_tv_lookup_error_is_503_on_mgm(mock_metadata_store):
    def _boom(query, params=None):
        raise RuntimeError("postgres down")

    mock_metadata_store.fetch_one = _boom
    resp = _mgm_app().get("/mgm", headers=_bearer(_token(sub="admin-1", role="admin")))
    assert resp.status_code == 503
    assert resp.json() == {"error": "auth check unavailable"}


def test_verify_runs_off_the_event_loop(mock_metadata_store):
    """The tv lookup blocks on Postgres; it must not run on the loop thread."""
    seen: list[bool] = []
    mock_metadata_store.token_versions = {"u1": 0}
    inner = mock_metadata_store.fetch_one

    def _recording(query, params=None):
        try:
            asyncio.get_running_loop()
            seen.append(True)
        except RuntimeError:
            seen.append(False)
        return inner(query, params)

    mock_metadata_store.fetch_one = _recording
    resp = _stats_app().get("/graph/stats", headers=_bearer(_token()))
    assert resp.status_code == 200
    assert seen and seen[0] is False


def test_requires_admin_real_paths():
    import auth

    class _Url:
        def __init__(self, path):
            self.path = path

    class _Req:
        def __init__(self, method, path):
            self.url = _Url(path)
            self.method = method

    admin = [
        ("GET", "/mgm"),
        ("GET", "/graph/assets/x"),
        ("GET", "/kg/settings"),
        ("POST", "/kg/settings"),
        ("DELETE", "/kg/settings/k"),
        ("GET", "/kg/stop-entities"),
        ("POST", "/kg/orphan-gc"),
        ("POST", "/graph/backfill"),
        ("POST", "/graph/backfill/temporal"),
        ("POST", "/codegraph/reindex"),
    ]
    member = [
        ("GET", "/graph/stats"),
        ("GET", "/graph/ego"),
        ("GET", "/graph/health"),
        ("GET", "/kgx"),
        ("POST", "/api/graph/x"),  # the phantom prefix is gone
    ]
    for method, path in admin:
        assert auth._requires_admin(_Req(method, path)) is True, (method, path)
    for method, path in member:
        assert auth._requires_admin(_Req(method, path)) is False, (method, path)


def test_member_refused_on_admin_routes(mock_metadata_store, mock_graph_store, monkeypatch):
    import graph.reconcile as gr
    import main

    calls: list = []

    def _run(limit_per_type=None):
        calls.append(limit_per_type)
        return {"totals": {}}

    monkeypatch.setattr(gr, "run_reconciliation", _run)
    mock_metadata_store.token_versions = {"u1": 0, "admin-1": 0}
    client = TestClient(main.app)
    member = _bearer(_token(sub="u1", role="user"))

    for method, path, kwargs in (
        ("POST", "/graph/backfill", {}),
        ("POST", "/kg/settings", {"json": {"settings": [{"key": "k", "value": "1"}]}}),
        ("POST", "/codegraph/reindex", {"json": {}}),
    ):
        resp = client.request(method, path, headers=member, **kwargs)
        assert resp.status_code == 403, (path, resp.text)
        assert resp.json() == {"error": "admin role required"}
    assert calls == []

    admin = _bearer(_token(sub="admin-1", role="admin"))
    ok = client.post("/graph/backfill", headers=admin)
    assert ok.status_code == 202, ok.text
    assert len(calls) == 1
