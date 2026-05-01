# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Tests for the stack-control sidecar."""

import os
import subprocess
from unittest.mock import patch, MagicMock

import pytest
from fastapi.testclient import TestClient

# Override host/.env when tests run under `docker compose run orchestrator`.
os.environ["RESTART_SECRET"] = "test-secret"

import main  # noqa: E402 — must come after env setup


@pytest.fixture
def client():
    return TestClient(main.app)


def _auth_headers():
    return {"X-Lumogis-Restart-Token": "test-secret"}


class TestHealth:
    def test_health_ok(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


class TestRestart:
    def test_missing_token_returns_403(self, client):
        resp = client.post("/restart")
        assert resp.status_code == 403

    def test_wrong_token_returns_403(self, client):
        resp = client.post("/restart", headers={"X-Lumogis-Restart-Token": "wrong"})
        assert resp.status_code == 403

    def test_valid_token_triggers_compose_restart(self, client):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "done"
        mock_result.stderr = ""
        with patch("main.subprocess.run", return_value=mock_result) as mock_run:
            resp = client.post("/restart", headers=_auth_headers())
        assert resp.status_code == 200
        assert resp.json()["status"] == "restarted"
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert "docker" in cmd
        assert "compose" in cmd
        assert "restart" in cmd

    def test_unknown_service_returns_400(self, client):
        resp = client.post(
            "/restart",
            json={"services": ["malicious-service"]},
            headers=_auth_headers(),
        )
        assert resp.status_code == 400
        assert "Unknown services" in resp.json()["detail"]

    def test_allowed_service_restart(self, client):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""
        mock_result.stderr = ""
        with patch("main.subprocess.run", return_value=mock_result) as mock_run:
            resp = client.post(
                "/restart",
                json={"services": ["orchestrator"]},
                headers=_auth_headers(),
            )
        assert resp.status_code == 200
        cmd = mock_run.call_args[0][0]
        assert "orchestrator" in cmd

    def test_recreate_uses_up_with_force_recreate(self, client):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""
        mock_result.stderr = ""
        with patch("main.subprocess.run", return_value=mock_result) as mock_run:
            resp = client.post(
                "/restart",
                json={"recreate": True, "services": ["orchestrator", "librechat"]},
                headers=_auth_headers(),
            )
        assert resp.status_code == 200
        cmd = mock_run.call_args[0][0]
        assert "up" in cmd
        assert "-d" in cmd
        assert "--no-deps" in cmd
        assert "--force-recreate" in cmd
        assert "orchestrator" in cmd
        assert "librechat" in cmd
        assert "restart" not in cmd

    def test_compose_failure_returns_500(self, client):
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = "compose error"
        with patch("main.subprocess.run", return_value=mock_result):
            resp = client.post("/restart", headers=_auth_headers())
        assert resp.status_code == 500
        assert "compose error" in resp.json()["detail"]

    def test_timeout_returns_504(self, client):
        with patch("main.subprocess.run", side_effect=subprocess.TimeoutExpired("cmd", 120)):
            resp = client.post("/restart", headers=_auth_headers())
        assert resp.status_code == 504

    def test_no_secret_configured_returns_503(self, client, monkeypatch):
        monkeypatch.setattr(main, "_RESTART_SECRET_ENV", "")
        with patch("main._PROJECT_ENV_FILE") as mock_path:
            mock_path.exists.return_value = False
            resp = client.post("/restart", headers=_auth_headers())
        assert resp.status_code == 503

    def test_secret_read_from_env_file(self, client, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("RESTART_SECRET=file-secret\n")
        with patch("main._PROJECT_ENV_FILE", env_file):
            resp = client.post(
                "/restart",
                headers={"X-Lumogis-Restart-Token": "file-secret"},
            )
        # token matches file secret — auth passes (compose will fail since not mocked)
        assert resp.status_code != 403
