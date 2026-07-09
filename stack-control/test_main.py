# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Tests for the stack-control sidecar."""

import json
import os
import subprocess
from unittest.mock import patch, MagicMock

import pytest
from fastapi.testclient import TestClient

# Override host/.env when tests run under `docker compose run orchestrator`.
os.environ["RESTART_SECRET"] = "test-secret"

import main  # noqa: E402 — must come after env setup

_ORIGINAL_CURRENT_RESTART_SECRET = main._current_restart_secret


@pytest.fixture(autouse=True)
def hermetic_restart_secret(monkeypatch):
    """Ignore live /project/.env in compose run — tests use a fixed token."""
    monkeypatch.setattr(main, "_current_restart_secret", lambda: "test-secret")


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
        monkeypatch.setattr(main, "_current_restart_secret", lambda: "")
        resp = client.post("/restart")
        assert resp.status_code == 503

    def test_secret_read_from_env_file(self, client, tmp_path, monkeypatch):
        env_file = tmp_path / ".env"
        env_file.write_text("RESTART_SECRET=file-secret\n")
        monkeypatch.setattr(main, "_current_restart_secret", _ORIGINAL_CURRENT_RESTART_SECRET)
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "done"
        mock_result.stderr = ""
        with patch("main._PROJECT_ENV_FILE", env_file):
            with patch("main.subprocess.run", return_value=mock_result) as mock_run:
                resp = client.post(
                    "/restart",
                    headers={"X-Lumogis-Restart-Token": "file-secret"},
                )
        assert resp.status_code == 200
        assert resp.json()["status"] == "restarted"
        mock_run.assert_called_once()

    def test_compose_cmd_uses_compose_file_from_project_env(self, client, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text(
            "COMPOSE_FILE=docker-compose.yml:docker-compose.override.yml\n"
        )
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""
        mock_result.stderr = ""
        with patch("main._PROJECT_ENV_FILE", env_file):
            with patch("main.subprocess.run", return_value=mock_result) as mock_run:
                resp = client.post("/restart", headers=_auth_headers())
        assert resp.status_code == 200
        cmd = mock_run.call_args[0][0]
        assert cmd.count("-f") == 2
        assert "docker-compose.yml" in cmd
        assert "docker-compose.override.yml" in cmd


class TestStatus:
    def test_missing_token_returns_403(self, client):
        resp = client.get("/status")
        assert resp.status_code == 403

    def test_wrong_token_returns_403(self, client):
        resp = client.get("/status", headers={"X-Lumogis-Restart-Token": "wrong"})
        assert resp.status_code == 403

    def test_valid_token_returns_compose_and_df(self, client):
        compose_stdout = json.dumps(
            [{"Service": "postgres", "State": "running", "Health": "healthy"}]
        )
        df_stdout = '{"Type":"Images","TotalCount":1}\n'
        mock_ps = MagicMock(returncode=0, stdout=compose_stdout, stderr="")
        mock_df = MagicMock(returncode=0, stdout=df_stdout, stderr="")

        def fake_run(cmd, **kwargs):
            if "ps" in cmd:
                return mock_ps
            return mock_df

        with patch("main.subprocess.run", side_effect=fake_run) as mock_run:
            resp = client.get("/status", headers=_auth_headers())
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["compose_ps"]) == 1
        assert body["compose_ps"][0]["Service"] == "postgres"
        assert body["system_df_busy"] is False
        assert "fetched_at" in body
        assert mock_run.call_count == 2
        assert mock_run.call_args_list[0][1]["cwd"] == main._project_dir()

    def test_compose_ps_timeout_returns_504(self, client):
        with patch(
            "main.subprocess.run",
            side_effect=subprocess.TimeoutExpired("cmd", 15),
        ):
            resp = client.get("/status", headers=_auth_headers())
        assert resp.status_code == 504

    def test_system_df_timeout_returns_504(self, client):
        mock_ps = MagicMock(returncode=0, stdout="[]", stderr="")

        def fake_run(cmd, **kwargs):
            if "ps" in cmd:
                return mock_ps
            raise subprocess.TimeoutExpired("cmd", 30)

        with patch("main.subprocess.run", side_effect=fake_run):
            resp = client.get("/status", headers=_auth_headers())
        assert resp.status_code == 504

    def test_no_secret_configured_returns_503(self, client, monkeypatch):
        monkeypatch.setattr(main, "_current_restart_secret", lambda: "")
        resp = client.get("/status")
        assert resp.status_code == 503
