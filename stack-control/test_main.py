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

    def test_non_ascii_token_returns_403(self, client):
        # Sent as latin-1 bytes; a str compare_digest would raise TypeError -> 500.
        resp = client.post(
            "/restart", headers={"X-Lumogis-Restart-Token": "tëst".encode("latin-1")}
        )
        assert resp.status_code == 403

    def test_token_check_uses_compare_digest(self, client):
        calls = []
        real = main.hmac.compare_digest

        def spy(a, b):
            calls.append((a, b))
            return real(a, b)

        mock_result = MagicMock(returncode=0, stdout="done", stderr="")
        with patch.object(main.hmac, "compare_digest", spy), patch(
            "main.subprocess.run", return_value=mock_result
        ):
            resp = client.post("/restart", headers=_auth_headers())
        assert resp.status_code == 200
        assert len(calls) == 1
        assert all(isinstance(x, bytes) for x in calls[0])

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


class TestProjectDir:
    """LUM-980: HOST_PROJECT_DIR is only usable when that path exists in the container."""

    def test_unset_uses_project(self, monkeypatch):
        monkeypatch.delenv("HOST_PROJECT_DIR", raising=False)
        assert main._project_dir() == "/project"

    def test_mounted_host_dir_is_used(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HOST_PROJECT_DIR", str(tmp_path))
        assert main._project_dir() == str(tmp_path)

    def test_unmounted_host_dir_falls_back_and_warns(self, monkeypatch, tmp_path, caplog):
        missing = tmp_path / "home" / "anna" / "lumogis"
        monkeypatch.setenv("HOST_PROJECT_DIR", str(missing))
        with caplog.at_level("WARNING", logger=main._log.name):
            assert main._project_dir() == "/project"
        assert "host_project_dir_not_mounted" in caplog.text

    def test_restart_runs_in_fallback_dir_when_unmounted(self, client, monkeypatch, tmp_path):
        """Without the fallback, subprocess.run(cwd=<missing>) raised and the route 500'd."""
        monkeypatch.setenv("HOST_PROJECT_DIR", str(tmp_path / "not-mounted"))
        ok = MagicMock(returncode=0, stdout="", stderr="")
        with patch("main.subprocess.run", return_value=ok) as mock_run:
            resp = client.post(
                "/restart", json={"services": ["orchestrator"]}, headers=_auth_headers()
            )
        assert resp.status_code == 200
        assert mock_run.call_args[1]["cwd"] == "/project"


class TestDefaultOverride:
    """LUM-980: with no COMPOSE_FILE in .env, restarts read the override like Compose does."""

    def _files(self, cmd: list[str]) -> list[str]:
        return [cmd[i + 1] for i, a in enumerate(cmd) if a == "-f"]

    def test_override_added_when_present(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HOST_PROJECT_DIR", str(tmp_path))
        (tmp_path / "docker-compose.override.yml").write_text("services: {}\n")
        with patch("main._PROJECT_ENV_FILE", tmp_path / ".env"):  # no .env
            files = self._files(main._compose_cmd(["ps"]))
        assert files == ["docker-compose.yml", "docker-compose.override.yml"]

    def test_no_override_file_keeps_default(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HOST_PROJECT_DIR", str(tmp_path))
        with patch("main._PROJECT_ENV_FILE", tmp_path / ".env"):
            assert self._files(main._compose_cmd(["ps"])) == ["docker-compose.yml"]

    def test_explicit_compose_file_is_used_as_written(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HOST_PROJECT_DIR", str(tmp_path))
        (tmp_path / "docker-compose.override.yml").write_text("services: {}\n")
        env = tmp_path / ".env"
        env.write_text("COMPOSE_FILE=docker-compose.yml:docker-compose.gpu.yml\n")
        with patch("main._PROJECT_ENV_FILE", env):
            files = self._files(main._compose_cmd(["ps"]))
        assert files == ["docker-compose.yml", "docker-compose.gpu.yml"]
