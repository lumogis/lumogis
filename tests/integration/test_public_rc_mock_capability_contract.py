# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""lumogis-mock-capability manifest + invoke contract v1 (RC compose)."""

from __future__ import annotations

import json
import os
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest

pytestmark = pytest.mark.integration

CONTRACT_VERSION = "1.0"
TOOL_NAME = "mock.echo_ping"
_REPO_ROOT = Path(__file__).resolve().parents[2]
_RC_COMPOSE_FILES = (
    "docker-compose.yml",
    "docker-compose.test.yml",
    "docker-compose.public-rc-stack.yml",
    "docker-compose.egress.yml",
)


def _mock_envelope(arguments: dict) -> dict:
    return {
        "contract_version": CONTRACT_VERSION,
        "tool": TOOL_NAME,
        "arguments": arguments,
        "meta": {"user": "rc", "request_id": f"rc-{uuid.uuid4().hex[:8]}"},
    }


@dataclass(frozen=True)
class _MockHttpResult:
    status_code: int
    body: dict | list | None
    text: str


def _mock_http_via_orchestrator(
    method: str,
    path: str,
    *,
    json_body: dict | None = None,
    headers: dict | None = None,
) -> _MockHttpResult:
    """Reach the contained mock on the isolated RC network (LUM-618)."""
    script = """
import json, os, sys
import httpx
req = json.load(sys.stdin)
base = os.environ.get("MOCK_INTERNAL_BASE", "http://lumogis-mock-capability:8080")
r = httpx.request(
    req["method"],
    base + req["path"],
    json=req.get("json"),
    headers=req.get("headers"),
    timeout=30.0,
)
body = None
if r.content:
    try:
        body = r.json()
    except ValueError:
        body = None
print(json.dumps({"status_code": r.status_code, "body": body, "text": r.text}))
"""
    cmd = [
        "docker",
        "compose",
        "--project-name",
        os.environ.get("COMPOSE_PROJECT_NAME", "lumogis-test"),
        "--project-directory",
        str(_REPO_ROOT),
    ]
    for compose_file in _RC_COMPOSE_FILES:
        cmd += ["-f", compose_file]
    cmd += ["exec", "-T", "orchestrator", "python", "-c", script]
    env = {**os.environ, "COMPOSE_PROFILES": "community-egress"}
    proc = subprocess.run(
        cmd,
        input=json.dumps(
            {"method": method, "path": path, "json": json_body, "headers": headers}
        ),
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            "mock capability probe via orchestrator failed:\n"
            f"stdout={proc.stdout!r}\nstderr={proc.stderr!r}"
        )
    data = json.loads(proc.stdout)
    return _MockHttpResult(
        status_code=int(data["status_code"]),
        body=data.get("body"),
        text=str(data.get("text") or ""),
    )


def _mock_http(
    method: str,
    path: str,
    *,
    json_body: dict | None = None,
    headers: dict | None = None,
) -> _MockHttpResult:
    """HTTP to mock capability — host port when published, else via orchestrator."""
    bases: list[str] = []
    env_base = os.environ.get("LUMOGIS_MOCK_CAPABILITY_BASE_URL", "").strip().rstrip("/")
    if env_base:
        bases.append(env_base)
    default_host = "http://127.0.0.1:18080"
    if default_host not in bases:
        bases.append(default_host)

    for base in bases:
        url = f"{base}{path}"
        try:
            with httpx.Client(timeout=30.0) as client:
                response = client.request(method, url, json=json_body, headers=headers)
        except httpx.ConnectError:
            continue
        body = None
        if response.content:
            try:
                body = response.json()
            except ValueError:
                body = None
        return _MockHttpResult(
            status_code=response.status_code,
            body=body,
            text=response.text,
        )

    return _mock_http_via_orchestrator(
        method, path, json_body=json_body, headers=headers
    )


@pytest.mark.public_rc
def test_mock_capability_manifest_and_echo():
    secret = os.environ.get(
        "MOCK_CAPABILITY_SHARED_SECRET",
        "rc-mock-cap-deterministic-secret-do-not-use-prod",
    ).strip()

    cap = _mock_http("GET", "/capabilities")
    assert cap.status_code == 200
    body = cap.body
    assert isinstance(body, dict)
    assert body.get("id") == "lumogis.mock.echo"
    assert body.get("contract_version") == CONTRACT_VERSION
    names = {t.get("name") for t in body.get("tools") or []}
    assert TOOL_NAME in names
    ping = next((t for t in body.get("tools") or [] if t.get("name") == TOOL_NAME), None)
    assert ping is not None
    assert isinstance(ping.get("description"), str)
    assert (ping.get("invoke") or {}).get("path") == f"/tools/{TOOL_NAME}"

    echo = _mock_http(
        "POST",
        f"/tools/{TOOL_NAME}",
        json_body=_mock_envelope({"rc": "ping"}),
        headers={"Authorization": f"Bearer {secret}"},
    )
    assert echo.status_code == 200
    payload = echo.body
    assert isinstance(payload, dict)
    assert payload.get("ok") is True
    assert payload.get("output") == {"echo": {"rc": "ping"}}

    missing = _mock_http(
        "POST",
        "/tools/mock.tool_does_not_exist",
        json_body=_mock_envelope({}),
        headers={"Authorization": f"Bearer {secret}"},
    )
    assert missing.status_code == 404

    bad_secret = _mock_http(
        "POST",
        f"/tools/{TOOL_NAME}",
        json_body=_mock_envelope({}),
        headers={"Authorization": "Bearer definitely-not-the-rc-secret"},
    )
    assert bad_secret.status_code == 403


@pytest.mark.public_rc
def test_core_tool_catalog_includes_mock_echo(api):
    r = api.get("/api/v1/me/tools")
    assert r.status_code == 200
    tools = r.json().get("tools") or []
    names = {t.get("name") for t in tools}
    assert TOOL_NAME in names
    row = next((t for t in tools if t.get("name") == TOOL_NAME), None)
    assert row is not None
    assert isinstance(row.get("description"), str)
