# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Unit tests for `routes/mgm.py`.

Contract under test:
  * GET /mgm returns 200 with text/html when the static file exists.
  * The response injects a `<script>window.LUMOGIS_CORE_BASE_URL = "...";</script>`
    into <head> reflecting the LUMOGIS_CORE_BASE_URL env var (this lets the
    SPA route entity/review ops to Core and graph ops to this service).
  * GET /mgm returns 404 (HTTPException) if the static file is missing.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient


def _client_with_mgm() -> TestClient:
    from routes.mgm import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_mgm_returns_html(monkeypatch):
    monkeypatch.setenv("LUMOGIS_CORE_BASE_URL", "")
    r = _client_with_mgm().get("/mgm")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert "<html" in r.text.lower() or "<!doctype" in r.text.lower()


def test_mgm_injects_lumogis_core_base_url_script(monkeypatch):
    """The injected <script> must contain the env var verbatim, JS-quoted."""
    monkeypatch.setenv("LUMOGIS_CORE_BASE_URL", "https://core.example.com")
    r = _client_with_mgm().get("/mgm")
    assert r.status_code == 200
    assert "window.LUMOGIS_CORE_BASE_URL" in r.text
    assert '"https://core.example.com"' in r.text


def test_mgm_injects_empty_string_when_unset(monkeypatch):
    """No env var → injects an empty string literal (not undefined)."""
    monkeypatch.delenv("LUMOGIS_CORE_BASE_URL", raising=False)
    r = _client_with_mgm().get("/mgm")
    assert r.status_code == 200
    assert 'window.LUMOGIS_CORE_BASE_URL = ""' in r.text


def test_mgm_returns_404_when_static_file_missing(monkeypatch):
    """Point _STATIC_HTML_PATH at a non-existent file → 404."""
    from routes import mgm

    monkeypatch.setattr(mgm, "_STATIC_HTML_PATH", Path("/tmp/does-not-exist.html"))
    r = _client_with_mgm().get("/mgm")
    assert r.status_code == 404
    assert "graph management page not found" in r.json()["detail"]
