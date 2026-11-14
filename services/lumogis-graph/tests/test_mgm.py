# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Unit tests for `routes/mgm.py`.

Contract under test:
  * GET /mgm returns 200 with text/html when the static file exists.
  * The response injects a `<script>window.LUMOGIS_CORE_BASE_URL = "...";</script>`
    into <head> reflecting the LUMOGIS_CORE_BASE_URL env var (this lets the
    SPA route entity/review ops to Core and graph ops to this service).
  * GET /mgm returns 404 (HTTPException) if the static file is missing.
  * GET /graph/assets/{name} serves the two vendored scripts (allowlist only,
    404 for anything else) and the served page references only those (LUM-941).
"""

from __future__ import annotations

import base64
import hashlib
import re
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


_ASSET_NAMES = ("cytoscape-3.28.1.min.js", "alpine-3.14.8.min.js")


def test_graph_asset_returns_js():
    from routes import mgm

    client = _client_with_mgm()
    for name in _ASSET_NAMES:
        r = client.get(f"/graph/assets/{name}")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/javascript")
        assert r.headers["cache-control"] == "max-age=31536000, immutable"
        assert r.content == (mgm._VENDOR_DIR / name).read_bytes()


def test_graph_asset_unknown_404():
    client = _client_with_mgm()
    assert client.get("/graph/assets/foo.js").status_code == 404
    # A real file in the vendor dir that is not allowlisted must not be served:
    # only the allowlist (not is_file) keeps it out.
    r = client.get("/graph/assets/LICENSES.md")
    assert r.status_code == 404
    assert r.json()["detail"] == "graph asset not found"


def test_graph_asset_traversal_404():
    client = _client_with_mgm()
    for path in ("/graph/assets/%2e%2e%2fgraph_mgm.html", "/graph/assets/graph_mgm.html"):
        r = client.get(path)
        assert r.status_code == 404
        assert "<html" not in r.text.lower()


def test_graph_asset_missing_file_404(monkeypatch, tmp_path):
    from routes import mgm

    monkeypatch.setattr(mgm, "_VENDOR_DIR", tmp_path)
    r = _client_with_mgm().get("/graph/assets/alpine-3.14.8.min.js")
    assert r.status_code == 404
    assert r.json()["detail"] == "graph asset not found"


def test_mgm_page_references_only_local_assets(monkeypatch):
    """The served page (after the base-URL injection) loads only vendored scripts."""
    from routes import mgm

    monkeypatch.setenv("LUMOGIS_CORE_BASE_URL", "")
    body = _client_with_mgm().get("/mgm").text
    assert "cdnjs.cloudflare.com" not in body
    assert "cdn.jsdelivr.net" not in body

    tags = re.findall(r"<script\s+src=\"(/graph/assets/[^\"]+)\"\s+integrity=\"([^\"]+)\"", body)
    assert [name for name, _ in tags] == [f"/graph/assets/{n}" for n in _ASSET_NAMES]
    for src, integrity in tags:
        data = (mgm._VENDOR_DIR / src.rsplit("/", 1)[1]).read_bytes()
        expected = "sha384-" + base64.b64encode(hashlib.sha384(data).digest()).decode()
        assert integrity == expected

    assert body.index("window.LUMOGIS_CORE_BASE_URL") < body.index("/graph/assets/")
