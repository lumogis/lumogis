# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""LUM-1002: Core regenerates librechat.yaml and restarts LibreChat only when the
``librechat`` Compose profile is on, and logs what stack-control actually answered.

Before, every Core start on a Release install asked stack-control to restart a
service that does not exist (500, "no such service: librechat") and logged success.
"""

from __future__ import annotations

import logging

import librechat_config
import pytest

from services import stack_control


class _Resp:
    def __init__(self, status_code):
        self.status_code = status_code


@pytest.fixture
def env_file(tmp_path, monkeypatch):
    from services import host_env

    path = tmp_path / ".env"
    monkeypatch.setattr(host_env, "ENV_PATH", path)
    monkeypatch.delenv("COMPOSE_PROFILES", raising=False)
    return path


@pytest.fixture
def calls(monkeypatch):
    seen = {"generate": 0, "post": []}

    def generate():
        seen["generate"] += 1
        return True

    def post(path, *, json=None, timeout):
        seen["post"].append((path, json))
        return seen.get("response", _Resp(200))

    monkeypatch.setattr(librechat_config, "generate_librechat_yaml", generate)
    monkeypatch.setattr(stack_control, "post", post)
    return seen


@pytest.mark.parametrize(
    "dotenv",
    ["", "COMPOSE_PROFILES=\n", "COMPOSE_PROFILES=graph\n", "COMPOSE_PROFILES=graph,stt\n"],
)
def test_nothing_happens_without_the_librechat_profile(env_file, calls, dotenv):
    env_file.write_text(dotenv)
    librechat_config.sync_librechat("test")
    assert calls["generate"] == 0 and calls["post"] == []


@pytest.mark.parametrize(
    "dotenv", ["COMPOSE_PROFILES=librechat\n", "COMPOSE_PROFILES=graph, librechat\n"]
)
def test_regenerates_and_restarts_when_the_profile_is_on(env_file, calls, dotenv):
    env_file.write_text(dotenv)
    librechat_config.sync_librechat("test")
    assert calls["generate"] == 1
    assert calls["post"] == [("/restart", {"services": ["librechat"]})]


def test_falls_back_to_cores_environment_when_env_file_is_missing(env_file, calls, monkeypatch):
    monkeypatch.setenv("COMPOSE_PROFILES", "librechat")
    librechat_config.sync_librechat("test")
    assert calls["post"] == [("/restart", {"services": ["librechat"]})]


def test_a_failed_restart_is_logged_as_a_failure_not_a_success(env_file, calls, caplog):
    env_file.write_text("COMPOSE_PROFILES=librechat\n")
    calls["response"] = _Resp(500)
    with caplog.at_level(logging.INFO, logger="librechat_config"):
        librechat_config.sync_librechat("test")
    messages = [r.getMessage() for r in caplog.records]
    assert any("restart failed" in m and "500" in m for m in messages)
    assert not any("restarted with the generated config" in m for m in messages)
