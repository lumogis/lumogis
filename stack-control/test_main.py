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


# ---------------------------------------------------------------------------
# LUM-331 — secure address endpoints
# ---------------------------------------------------------------------------

IMAGE_ID = "sha256:" + "a" * 64


class _FakeRun:
    """Answers subprocess.run by command shape; records every argv."""

    def __init__(self, responses):
        self.responses = responses  # list of (predicate, CompletedProcess-or-exception)
        self.calls: list[list[str]] = []
        self.kwargs: list[dict] = []

    def __call__(self, cmd, **kwargs):
        self.calls.append(list(cmd))
        self.kwargs.append(kwargs)
        for pred, resp in self.responses:
            if pred(cmd):
                if isinstance(resp, BaseException):
                    raise resp
                return resp
        return subprocess.CompletedProcess(cmd, 1, "", "unexpected command")


def _cp(rc=0, out="", err=""):
    return subprocess.CompletedProcess([], rc, out, err)


def _is(*tokens):
    return lambda cmd: all(t in cmd for t in tokens)


def _desktop(name="Ubuntu 24.04 LTS"):
    return (_is("info"), _cp(0, name + "\n"))


def _own_image():
    return [
        (_is("ps", "-q"), _cp(0, "c0ffee\n")),
        (_is("inspect"), _cp(0, IMAGE_ID + "\n")),
    ]


ALLOWED_HELPER_FLAGS = set(main.HOST_HELPER_FLAGS)
FORBIDDEN_FLAGS = {"--privileged", "-v", "--volume", "--mount", "--device", "--cap-add", "--pid"}


class TestHostLanAddress:
    def test_requires_token(self, client):
        assert client.get("/host-lan-address").status_code == 403

    def test_returns_rfc1918_and_runs_fixed_argv(self, client):
        fake = _FakeRun([_desktop(), *_own_image(), (_is("run"), _cp(0, "192.168.1.20\n"))])
        with patch("main.subprocess.run", fake):
            resp = client.get("/host-lan-address", headers=_auth_headers())
        assert resp.json() == {"address": "192.168.1.20", "reason": None}
        (run_cmd,) = [c for c in fake.calls if c[:2] == ["docker", "run"]]
        image_at = run_cmd.index(IMAGE_ID)
        flags = run_cmd[2:image_at]
        assert tuple(flags) == main.HOST_HELPER_FLAGS
        assert set(f for f in flags if f.startswith("-")) <= ALLOWED_HELPER_FLAGS
        assert not FORBIDDEN_FLAGS & set(run_cmd)
        assert ["--pull", "never"] == run_cmd[run_cmd.index("--pull") : run_cmd.index("--pull") + 2]
        assert run_cmd[image_at + 1 :] == ["-c", main.HOST_HELPER_PROGRAM]
        assert run_cmd[image_at + 2] is main.HOST_HELPER_PROGRAM

    def test_docker_desktop_never_runs_helper(self, client):
        fake = _FakeRun([_desktop("Docker Desktop"), *_own_image()])
        with patch("main.subprocess.run", fake):
            resp = client.get("/host-lan-address", headers=_auth_headers())
        assert resp.json() == {"address": None, "reason": "docker_desktop"}
        assert not [c for c in fake.calls if "run" in c]

    def test_image_missing(self, client):
        fake = _FakeRun([_desktop(), (_is("ps", "-q"), _cp(0, ""))])
        with patch("main.subprocess.run", fake):
            resp = client.get("/host-lan-address", headers=_auth_headers())
        assert resp.json() == {"address": None, "reason": "helper_image_missing"}
        assert not [c for c in fake.calls if c[:2] == ["docker", "run"]]

    def test_image_id_must_be_a_digest(self, client):
        fake = _FakeRun(
            [
                _desktop(),
                (_is("ps", "-q"), _cp(0, "c0ffee\n")),
                (_is("inspect"), _cp(0, "evil:tag\n")),
            ]
        )
        with patch("main.subprocess.run", fake):
            resp = client.get("/host-lan-address", headers=_auth_headers())
        assert resp.json()["reason"] == "helper_image_missing"

    @pytest.mark.parametrize(
        "out,reason",
        [
            ("172.17.0.1junk\n", "failed"),
            ("8.8.8.8\n", "not_rfc1918"),
            ("100.64.0.1\n", "not_rfc1918"),
            ("", "failed"),
        ],
    )
    def test_rejects_non_rfc1918_or_junk(self, client, out, reason):
        fake = _FakeRun([_desktop(), *_own_image(), (_is("run"), _cp(0, out))])
        with patch("main.subprocess.run", fake):
            resp = client.get("/host-lan-address", headers=_auth_headers())
        assert resp.json() == {"address": None, "reason": reason}

    def test_timeout_is_failed(self, client):
        fake = _FakeRun(
            [_desktop(), *_own_image(), (_is("run"), subprocess.TimeoutExpired("docker", 15))]
        )
        with patch("main.subprocess.run", fake):
            resp = client.get("/host-lan-address", headers=_auth_headers())
        assert resp.json() == {"address": None, "reason": "failed"}


class TestCaddyApply:
    def test_requires_token(self, client):
        assert client.post("/caddy/apply").status_code == 403

    def test_caddy_not_running(self, client):
        fake = _FakeRun([(_is("exec", "true"), _cp(1, "", "service caddy is not running"))])
        with patch("main.subprocess.run", fake):
            assert client.post("/caddy/apply", headers=_auth_headers()).json() == {
                "ok": False,
                "code": "caddy_unreachable",
            }

    def test_pending_not_visible_means_volume_missing(self, client):
        fake = _FakeRun([(_is("exec", "true"), _cp(0)), (_is("test", "-f"), _cp(1))])
        with patch("main.subprocess.run", fake):
            assert client.post("/caddy/apply", headers=_auth_headers()).json()["code"] == (
                "caddy_volume_missing"
            )

    @pytest.mark.parametrize(
        "rc,code",
        [
            (127, "caddy_image_outdated"),
            (126, "caddy_image_outdated"),
            (1, "caddy_snippet_rejected"),
        ],
    )
    def test_validate_failures(self, client, rc, code):
        fake = _FakeRun(
            [
                (_is("exec", "true"), _cp(0)),
                (_is("test", "-f"), _cp(0)),
                (_is("/etc/caddy/validate-home.sh"), _cp(rc)),
            ]
        )
        with patch("main.subprocess.run", fake):
            assert client.post("/caddy/apply", headers=_auth_headers()).json() == {
                "ok": False,
                "code": code,
            }

    def test_ok_checks_the_pending_file(self, client):
        fake = _FakeRun(
            [
                (_is("exec", "true"), _cp(0)),
                (_is("test", "-f"), _cp(0)),
                (_is("/etc/caddy/validate-home.sh"), _cp(0)),
            ]
        )
        with patch("main.subprocess.run", fake):
            assert client.post("/caddy/apply", headers=_auth_headers()).json() == {
                "ok": True,
                "code": "validated",
            }
        assert any(main.CADDY_PENDING_FILE in c for c in fake.calls)
        assert all(c[:2] == ["docker", "compose"] for c in fake.calls)


class TestCaddyReload:
    def test_requires_token(self, client):
        assert client.post("/caddy/reload").status_code == 403

    def test_ok_clears_the_start_without_household_marker(self, client):
        fake = _FakeRun([(_is("caddy", "reload"), _cp(0)), (_is("rm"), _cp(0))])
        with patch("main.subprocess.run", fake):
            assert client.post("/caddy/reload", headers=_auth_headers()).json() == {
                "ok": True,
                "code": "reloaded",
            }
        rm = [c for c in fake.calls if "rm" in c]
        assert len(rm) == 1 and rm[0][-3:] == ["rm", "-f", main.CADDY_REJECTED_MARKER]

    def test_failure_keeps_the_marker(self, client):
        fake = _FakeRun([(_is("caddy", "reload"), _cp(1, "", "boom"))])
        with patch("main.subprocess.run", fake):
            client.post("/caddy/reload", headers=_auth_headers())
        assert not [c for c in fake.calls if "rm" in c]

    def test_failure_never_returns_stderr_and_logs_redacted(self, client, caplog):
        bearer = "B" * 50
        stderr = (
            f"Error: adapting config: password {bearer}\n"
            '{"password":"hunter2-secret"} X-Api-Key: k3y-v4lue'
        )
        fake = _FakeRun([(_is("caddy", "reload"), _cp(1, "", stderr))])
        with caplog.at_level("WARNING"), patch("main.subprocess.run", fake):
            resp = client.post("/caddy/reload", headers=_auth_headers())
        assert resp.json() == {"ok": False, "code": "reload_failed"}
        assert bearer not in resp.text and "hunter2" not in resp.text
        logged = caplog.text
        for secret in (bearer, "hunter2-secret", "k3y-v4lue"):
            assert secret not in logged
        assert "reload failed" in logged


class TestCaddyRestart:
    """LUM-1004: a CA switch needs a Caddy restart (a reload keeps the cached cert)."""

    def test_requires_token(self, client):
        assert client.post("/caddy/restart").status_code == 403

    def test_ok_runs_compose_restart_caddy_only(self, client):
        fake = _FakeRun([(_is("restart", "caddy"), _cp(0))])
        with patch("main.subprocess.run", fake):
            assert client.post("/caddy/restart", headers=_auth_headers()).json() == {
                "ok": True,
                "code": "restarted",
            }
        assert len(fake.calls) == 1
        cmd = fake.calls[0]
        assert cmd[-2:] == ["restart", "caddy"] and "exec" not in cmd

    def test_failure_never_returns_stderr_and_logs_redacted(self, client, caplog):
        bearer = "B" * 50
        fake = _FakeRun([(_is("restart", "caddy"), _cp(1, "", f"error X-Api-Key: {bearer}"))])
        with caplog.at_level("WARNING"), patch("main.subprocess.run", fake):
            resp = client.post("/caddy/restart", headers=_auth_headers())
        assert resp.json() == {"ok": False, "code": "restart_failed"}
        assert bearer not in resp.text and bearer not in caplog.text
        assert "caddy restart failed" in caplog.text


class TestCaddyHomeStatus:
    def test_requires_token(self, client):
        assert client.get("/caddy/home-status").status_code == 403

    @pytest.mark.parametrize(
        "out,present,rejected",
        [("0\n1\n", True, False), ("1\n1\n", False, False), ("0\n0\n", True, True)],
    )
    def test_parses(self, client, out, present, rejected):
        fake = _FakeRun([(_is("sh", "-c"), _cp(0, out))])
        with patch("main.subprocess.run", fake):
            resp = client.get("/caddy/home-status", headers=_auth_headers())
        assert resp.json() == {"present": present, "rejected_at_start": rejected, "reachable": True}
        (cmd,) = fake.calls
        assert cmd[-1] is main._HOME_STATUS_SCRIPT

    def test_unreachable(self, client):
        fake = _FakeRun([(_is("sh", "-c"), _cp(1, "", "not running"))])
        with patch("main.subprocess.run", fake):
            resp = client.get("/caddy/home-status", headers=_auth_headers())
        assert resp.json()["reachable"] is False


def test_restart_allow_list_unchanged():
    """LUM-331 adds no service to the restart allow-list (Caddy is reloaded, never recreated)."""
    assert "caddy" not in main._ALLOWED_SERVICES


# ---------------------------------------------------------------------------
# LUM-989 — the compose environment, and the knowledge graph start / stop
# ---------------------------------------------------------------------------


class TestComposeEnv:
    """Compose gives the process environment precedence over .env; the sidecar's
    own environment is a snapshot of .env from the day it started."""

    def test_keys_in_project_env_are_removed_from_the_environment(self, tmp_path, monkeypatch):
        env_file = tmp_path / ".env"
        env_file.write_text("GRAPH_MODE=service\nexport COMPOSE_PROFILES=graph\n# FOO=1\n")
        monkeypatch.setattr(main, "_PROJECT_ENV_FILE", env_file)
        monkeypatch.setattr(main, "_STARTUP_ENV_KEYS", frozenset())
        monkeypatch.setenv("GRAPH_MODE", "disabled")  # stale: the sidecar started before the change
        monkeypatch.setenv("COMPOSE_PROFILES", "")
        monkeypatch.setenv("FOO", "kept: only a comment in .env")
        monkeypatch.setenv("PATH_LIKE_KEPT", "yes")
        env = main._compose_env()
        assert "GRAPH_MODE" not in env and "COMPOSE_PROFILES" not in env
        assert env["FOO"] == "kept: only a comment in .env"
        assert env["PATH_LIKE_KEPT"] == "yes"

    def test_a_key_only_in_the_startup_snapshot_is_dropped_too(self, tmp_path, monkeypatch):
        env_file = tmp_path / ".env"
        env_file.write_text("OTHER=1\n")  # GRAPH_MODE removed from the file since start-up
        monkeypatch.setattr(main, "_PROJECT_ENV_FILE", env_file)
        monkeypatch.setattr(main, "_STARTUP_ENV_KEYS", frozenset({"GRAPH_MODE"}))
        monkeypatch.setenv("GRAPH_MODE", "disabled")
        assert "GRAPH_MODE" not in main._compose_env()

    def test_missing_env_file_keeps_only_the_startup_drop(self, tmp_path, monkeypatch):
        monkeypatch.setattr(main, "_PROJECT_ENV_FILE", tmp_path / "absent.env")
        monkeypatch.setattr(main, "_STARTUP_ENV_KEYS", frozenset())
        monkeypatch.setenv("SOMETHING", "x")
        assert main._compose_env()["SOMETHING"] == "x"

    def test_restart_passes_the_compose_env(self, client, tmp_path, monkeypatch):
        env_file = tmp_path / ".env"
        env_file.write_text("GRAPH_MODE=service\n")
        monkeypatch.setattr(main, "_PROJECT_ENV_FILE", env_file)
        monkeypatch.setenv("GRAPH_MODE", "disabled")
        with patch("main.subprocess.run", return_value=_cp(0)) as run:
            assert client.post("/restart", headers=_auth_headers()).status_code == 200
        assert "GRAPH_MODE" not in run.call_args.kwargs["env"]

    def test_status_and_caddy_calls_pass_the_compose_env(self, client, tmp_path, monkeypatch):
        env_file = tmp_path / ".env"
        env_file.write_text("GRAPH_MODE=service\n")
        monkeypatch.setattr(main, "_PROJECT_ENV_FILE", env_file)
        monkeypatch.setenv("GRAPH_MODE", "disabled")
        fake = _FakeRun([(_is("sh", "-c"), _cp(0, "0\n1\n"))])
        with patch("main.subprocess.run", fake):
            client.get("/caddy/home-status", headers=_auth_headers())
        assert "GRAPH_MODE" not in fake.kwargs[0]["env"]


def _compose_args(cmd):
    """The part of a compose argv after the -f/-p flags."""
    i = cmd.index("compose") + 1
    while i < len(cmd) and cmd[i] in ("-f", "-p"):
        i += 2
    return cmd[i:]


class TestGraphStart:
    def test_requires_token(self, client):
        assert client.post("/graph/start").status_code == 403
        assert client.post("/graph/start", headers={"X-Lumogis-Restart-Token": "x"}).status_code == 403

    def test_runs_the_two_fixed_commands_in_order(self, client):
        fake = _FakeRun([(lambda c: True, _cp(0))])
        with patch("main.subprocess.run", fake):
            resp = client.post("/graph/start", headers=_auth_headers())
        assert resp.json() == {"ok": True, "code": "started"}
        assert [_compose_args(c) for c in fake.calls] == [
            ["up", "-d", "--no-build", "falkordb", "lumogis-graph"],
            ["up", "-d", "--no-build", "--no-deps", "--force-recreate", "backup", "orchestrator"],
        ]
        assert all("--build" not in c for c in fake.calls)

    def test_every_command_gets_the_compose_env(self, client, tmp_path, monkeypatch):
        env_file = tmp_path / ".env"
        env_file.write_text("GRAPH_MODE=service\nCOMPOSE_PROFILES=graph\n")
        monkeypatch.setattr(main, "_PROJECT_ENV_FILE", env_file)
        monkeypatch.setenv("GRAPH_MODE", "disabled")
        monkeypatch.setenv("COMPOSE_PROFILES", "")
        fake = _FakeRun([(lambda c: True, _cp(0))])
        with patch("main.subprocess.run", fake):
            client.post("/graph/start", headers=_auth_headers())
        assert len(fake.kwargs) == 2
        for kw in fake.kwargs:
            assert "GRAPH_MODE" not in kw["env"] and "COMPOSE_PROFILES" not in kw["env"]

    def test_a_failure_in_the_first_step_stops_the_sequence(self, client):
        fake = _FakeRun([(_is("falkordb"), _cp(1, "", "pull access denied"))])
        with patch("main.subprocess.run", fake):
            resp = client.post("/graph/start", headers=_auth_headers())
        assert resp.json() == {"ok": False, "code": "compose_failed"}
        assert len(fake.calls) == 1

    def test_a_failure_in_the_recreate_step_is_reported(self, client):
        fake = _FakeRun(
            [(_is("--force-recreate"), _cp(1, "", "nope")), (lambda c: True, _cp(0))]
        )
        with patch("main.subprocess.run", fake):
            resp = client.post("/graph/start", headers=_auth_headers())
        assert resp.json() == {"ok": False, "code": "compose_failed"}

    def test_timeout_is_reported_with_the_pull_budget(self, client):
        fake = _FakeRun([(lambda c: True, subprocess.TimeoutExpired("docker", 1500))])
        with patch("main.subprocess.run", fake):
            resp = client.post("/graph/start", headers=_auth_headers())
        assert resp.json() == {"ok": False, "code": "timeout"}
        assert fake.kwargs[0]["timeout"] == 1500

    def test_stderr_is_never_returned_and_the_log_is_redacted(self, client, caplog):
        secret = "S" * 50
        fake = _FakeRun([(lambda c: True, _cp(1, "", f"failed password hunter2 {secret}"))])
        with patch("main.subprocess.run", fake), caplog.at_level("WARNING"):
            resp = client.post("/graph/start", headers=_auth_headers())
        assert "hunter2" not in resp.text and secret not in resp.text
        logged = " ".join(r.getMessage() for r in caplog.records)
        assert "hunter2" not in logged and secret not in logged

    def test_a_second_sequence_while_one_runs_is_busy_and_runs_nothing(self, client):
        assert main._graph_lock.acquire(blocking=False)
        try:
            fake = _FakeRun([(lambda c: True, _cp(0))])
            with patch("main.subprocess.run", fake):
                start = client.post("/graph/start", headers=_auth_headers())
                stop = client.post("/graph/stop", headers=_auth_headers())
        finally:
            main._graph_lock.release()
        assert start.json() == {"ok": False, "code": "busy"}
        assert stop.json() == {"ok": False, "code": "busy"}
        assert fake.calls == []

    def test_the_lock_is_released_after_a_failure(self, client):
        fake = _FakeRun([(lambda c: True, _cp(1, "", "x"))])
        with patch("main.subprocess.run", fake):
            client.post("/graph/start", headers=_auth_headers())
        assert main._graph_lock.acquire(blocking=False)
        main._graph_lock.release()

    def test_takes_no_request_body(self, client):
        fake = _FakeRun([(lambda c: True, _cp(0))])
        with patch("main.subprocess.run", fake):
            resp = client.post(
                "/graph/start", headers=_auth_headers(), json={"services": ["postgres; rm -rf /"]}
            )
        assert resp.json()["ok"] is True
        assert not any("postgres" in " ".join(c) and "rm -rf" in " ".join(c) for c in fake.calls)


class TestGraphStop:
    def test_requires_token(self, client):
        assert client.post("/graph/stop").status_code == 403

    def test_stop_rm_then_recreate(self, client):
        fake = _FakeRun([(lambda c: True, _cp(0))])
        with patch("main.subprocess.run", fake):
            resp = client.post("/graph/stop", headers=_auth_headers())
        assert resp.json() == {"ok": True, "code": "stopped"}
        assert [_compose_args(c) for c in fake.calls] == [
            ["stop", "falkordb", "lumogis-graph"],
            ["rm", "-f", "falkordb", "lumogis-graph"],
            ["up", "-d", "--no-build", "--no-deps", "--force-recreate", "backup", "orchestrator"],
        ]

    def test_a_failed_stop_does_not_recreate(self, client):
        fake = _FakeRun([(_is("stop"), _cp(1, "", "daemon error"))])
        with patch("main.subprocess.run", fake):
            resp = client.post("/graph/stop", headers=_auth_headers())
        assert resp.json() == {"ok": False, "code": "compose_failed"}
        assert len(fake.calls) == 1

    def test_stop_timeout(self, client):
        fake = _FakeRun([(_is("stop"), subprocess.TimeoutExpired("docker", 120))])
        with patch("main.subprocess.run", fake):
            resp = client.post("/graph/stop", headers=_auth_headers())
        assert resp.json() == {"ok": False, "code": "timeout"}


def test_graph_endpoints_do_not_widen_the_restart_allow_list():
    assert main._ALLOWED_SERVICES == {
        "orchestrator", "librechat", "ollama", "qdrant", "postgres", "mongodb"
    }
    assert main.GRAPH_SERVICES == ("falkordb", "lumogis-graph")
