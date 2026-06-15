# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""``/api/v1/admin/ollama/*`` — typed Ollama admin routes (LUM-451)."""

from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    import main

    with TestClient(main.app) as c:
        yield c


def _auth_header(monkeypatch: pytest.MonkeyPatch, user_id: str, role: str = "admin") -> dict[str, str]:
    monkeypatch.setenv("AUTH_SECRET", "test-admin-ollama-v1-secret")
    monkeypatch.setenv("AUTH_ENABLED", "true")
    from auth import mint_access_token

    tok = mint_access_token(user_id, role)
    return {"Authorization": f"Bearer {tok}"}


def test_discovery_401_without_auth(client, monkeypatch) -> None:
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTH_SECRET", "test-admin-ollama-401")
    r = client.get("/api/v1/admin/ollama/discovery")
    assert r.status_code == 401


def test_discovery_403_non_admin(client, monkeypatch) -> None:
    hdr = _auth_header(monkeypatch, "bob", "user")
    r = client.get("/api/v1/admin/ollama/discovery", headers=hdr)
    assert r.status_code == 403


def test_discovery_200_admin(client, monkeypatch) -> None:
    hdr = _auth_header(monkeypatch, "admin")
    payload = {
        "local": [],
        "catalog": [],
        "alias_map": {},
        "embedding_model": "nomic-embed-text",
        "default_model": "llama",
    }
    with patch("services.admin_ollama.build_ollama_discovery", return_value=payload):
        r = client.get("/api/v1/admin/ollama/discovery", headers=hdr)
    assert r.status_code == 200
    assert r.json()["embedding_model"] == "nomic-embed-text"


def test_v1_discovery_json_matches_legacy(client, monkeypatch) -> None:
    hdr = _auth_header(monkeypatch, "admin")
    payload = {
        "local": [{"name": "llama3.2:3b", "display_name": "Llama 3.2"}],
        "catalog": [{"name": "phi3", "installed": False, "display_name": "Phi 3"}],
        "alias_map": {"llama3.2:3b": "llama"},
        "embedding_model": "nomic-embed-text",
        "default_model": "llama",
    }
    with patch("services.admin_ollama.build_ollama_discovery", return_value=payload):
        legacy = client.get("/settings/ollama-discovery", headers=hdr)
        v1 = client.get("/api/v1/admin/ollama/discovery", headers=hdr)
    assert legacy.status_code == 200
    assert v1.status_code == 200
    assert legacy.json() == v1.json()


def test_pull_async_202_returns_job_id(client, monkeypatch) -> None:
    hdr = _auth_header(monkeypatch, "admin")
    with (
        patch("routes.admin_ollama.create_job", return_value="job-abc"),
        patch("routes.admin_ollama.run_pull_job"),
    ):
        r = client.post(
            "/api/v1/admin/ollama/pull/async",
            headers=hdr,
            json={"name": "tinyllama"},
        )
    assert r.status_code == 202
    body = r.json()
    assert body["status"] == "started"
    assert body["job_id"] == "job-abc"


def test_pull_async_409_when_job_running(client, monkeypatch) -> None:
    from services.ollama_pull_jobs import JobAlreadyRunning

    hdr = _auth_header(monkeypatch, "admin")
    with patch("routes.admin_ollama.create_job", side_effect=JobAlreadyRunning):
        r = client.post(
            "/api/v1/admin/ollama/pull/async",
            headers=hdr,
            json={"name": "tinyllama"},
        )
    assert r.status_code == 409


def test_pull_async_400_invalid_name(client, monkeypatch) -> None:
    hdr = _auth_header(monkeypatch, "admin")
    r = client.post(
        "/api/v1/admin/ollama/pull/async",
        headers=hdr,
        json={"name": "bad name!"},
    )
    assert r.status_code == 400


def test_pull_job_404_unknown(client, monkeypatch) -> None:
    hdr = _auth_header(monkeypatch, "admin")
    with patch("routes.admin_ollama.get_job", return_value=None):
        r = client.get("/api/v1/admin/ollama/pull/jobs/missing-id", headers=hdr)
    assert r.status_code == 404


def test_delete_200_admin(client, monkeypatch) -> None:
    hdr = _auth_header(monkeypatch, "admin")
    with patch(
        "services.admin_ollama.delete_model",
        return_value={"status": "deleted", "name": "tinyllama"},
    ):
        r = client.post(
            "/api/v1/admin/ollama/delete",
            headers=hdr,
            json={"name": "tinyllama"},
        )
    assert r.status_code == 200
    assert r.json() == {"status": "deleted", "name": "tinyllama"}


def test_legacy_sync_pull_still_200(client, monkeypatch) -> None:
    hdr = _auth_header(monkeypatch, "admin")
    with patch(
        "services.admin_ollama.sync_pull_model",
        return_value={
            "status": "pulled",
            "name": "tinyllama",
            "qdrant_init_warning": None,
        },
    ):
        r = client.post("/settings/ollama-pull", headers=hdr, json={"name": "tinyllama"})
    assert r.status_code == 200
    assert r.json()["status"] == "pulled"


class TestAdminOllamaService(unittest.TestCase):
    def test_validate_model_name_rejects_empty(self):
        from services.admin_ollama import validate_model_name

        with self.assertRaises(Exception) as ctx:
            validate_model_name("  ")
        self.assertEqual(getattr(ctx.exception, "status_code", None), 400)

    def test_build_ollama_discovery_shape(self):
        from services.admin_ollama import build_ollama_discovery

        local_models = [{"name": "llama3.2:3b", "size": 1_000_000}]
        catalog = [{"name": "phi3", "description": "test"}]
        all_models = {
            "llama": {
                "model": "llama3.2:3b",
                "base_url": "http://ollama:11434",
            },
        }
        store = MagicMock()

        with (
            patch("ollama_client.list_local_models", return_value=local_models),
            patch("ollama_client.fetch_catalog", return_value=catalog),
            patch("config.get_all_models_config", return_value=all_models),
            patch("config.get_metadata_store", return_value=store),
            patch("services.admin_ollama._safe_get_setting", return_value="llama"),
            patch("services.admin_ollama._safe_is_enabled", return_value=True),
            patch.dict(os.environ, {"EMBEDDING_MODEL": "nomic-embed-text"}),
        ):
            response = build_ollama_discovery()

        self.assertIn("embedding_model", response)
        self.assertIn("default_model", response)
        self.assertIn("alias_map", response)
