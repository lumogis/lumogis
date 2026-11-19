# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""services.stack_control — the single stack-control client (LUM-331 Pass 1)."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from services import stack_control


@pytest.fixture
def env_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    f = tmp_path / ".env"
    monkeypatch.setattr(stack_control, "PROJECT_ENV_FILE", f)
    return f


def test_token_from_env_file_wins_over_env_var(env_file: Path, monkeypatch) -> None:
    env_file.write_text("FOO=1\n  RESTART_SECRET= live-secret \nBAR=2\n")
    monkeypatch.setenv("RESTART_SECRET", "stale-placeholder")
    assert stack_control.token() == "live-secret"


def test_token_falls_back_to_env_var_without_file(env_file: Path, monkeypatch) -> None:
    monkeypatch.setenv("RESTART_SECRET", "from-env")
    assert not env_file.exists()
    assert stack_control.token() == "from-env"


def test_token_falls_back_when_file_has_no_secret(env_file: Path, monkeypatch) -> None:
    env_file.write_text("OTHER=1\n")
    monkeypatch.setenv("RESTART_SECRET", "from-env")
    assert stack_control.token() == "from-env"


def test_token_empty_when_nothing_set(env_file: Path, monkeypatch) -> None:
    monkeypatch.delenv("RESTART_SECRET", raising=False)
    assert stack_control.token() == ""


def test_base_url_default_and_override(monkeypatch) -> None:
    monkeypatch.delenv("STACK_CONTROL_URL", raising=False)
    assert stack_control.base_url() == "http://stack-control:9000"
    monkeypatch.setenv("STACK_CONTROL_URL", "http://sc.test:9000/")
    assert stack_control.base_url() == "http://sc.test:9000"


def _capture(monkeypatch, env_file: Path) -> tuple[list[httpx.Request], list[float]]:
    """Route httpx through a MockTransport; record every request and timeout."""
    env_file.write_text("RESTART_SECRET=tok123\n")
    monkeypatch.setenv("STACK_CONTROL_URL", "http://sc.test:9000")
    seen: list[httpx.Request] = []
    timeouts: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(handler)
    real_client = httpx.Client

    def client_factory(*args, **kwargs):
        timeouts.append(kwargs["timeout"])
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)

    def fake_post(url, **kw):
        timeouts.append(kw["timeout"])
        return real_client(transport=transport).post(
            url, headers=kw.get("headers"), json=kw.get("json")
        )

    monkeypatch.setattr(httpx, "Client", client_factory)
    monkeypatch.setattr(httpx, "post", fake_post)
    return seen, timeouts


def test_post_sends_token_header_and_json(monkeypatch, env_file: Path) -> None:
    seen, timeouts = _capture(monkeypatch, env_file)
    r = stack_control.post("/restart", json={"services": ["librechat"]}, timeout=120.0)
    assert r.status_code == 200
    (req,) = seen
    assert req.method == "POST"
    assert str(req.url) == "http://sc.test:9000/restart"
    assert req.headers[stack_control.TOKEN_HEADER] == "tok123"
    assert json.loads(req.read()) == {"services": ["librechat"]}
    assert timeouts == [120.0]


def test_get_sends_token_header(monkeypatch, env_file: Path) -> None:
    seen, timeouts = _capture(monkeypatch, env_file)
    r = stack_control.get("/status", timeout=45.0)
    assert r.json() == {"ok": True}
    (req,) = seen
    assert req.method == "GET"
    assert str(req.url) == "http://sc.test:9000/status"
    assert req.headers[stack_control.TOKEN_HEADER] == "tok123"
    assert timeouts == [45.0]


def test_no_other_module_defines_its_own_stack_control_client() -> None:
    """The URL, the token header and the token lookup live only in stack_control.py."""
    import re

    root = Path(stack_control.__file__).resolve().parents[1]
    pattern = re.compile(
        r"_STACK_CONTROL_URL\b|_restart_secret\b|_current_restart_secret\b"
        r"|X-Lumogis-Restart-Token|stack-control:9000"
    )
    offenders = []
    for py in root.rglob("*.py"):
        rel = py.relative_to(root).as_posix()
        if rel.startswith("tests/") or rel == "services/stack_control.py":
            continue
        if pattern.search(py.read_text(encoding="utf-8", errors="ignore")):
            offenders.append(rel)
    assert offenders == []
