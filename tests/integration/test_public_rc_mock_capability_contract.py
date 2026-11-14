# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""lumogis-mock-capability manifest + invoke contract v1 (RC compose)."""

from __future__ import annotations

import os
import uuid

import httpx
import pytest

from _reachability import skip_unless_reachable

pytestmark = pytest.mark.integration

CONTRACT_VERSION = "1.0"
TOOL_NAME = "mock.echo_ping"


def _mock_envelope(arguments: dict) -> dict:
    return {
        "contract_version": CONTRACT_VERSION,
        "tool": TOOL_NAME,
        "arguments": arguments,
        "meta": {"user": "rc", "request_id": f"rc-{uuid.uuid4().hex[:8]}"},
    }


@pytest.mark.public_rc
def test_mock_capability_manifest_and_echo():
    base = os.environ.get("LUMOGIS_MOCK_CAPABILITY_BASE_URL", "http://127.0.0.1:18080").strip().rstrip("/")
    secret = os.environ.get(
        "MOCK_CAPABILITY_SHARED_SECRET",
        "rc-mock-cap-deterministic-secret-do-not-use-prod",
    ).strip()

    skip_unless_reachable(f"{base}/capabilities", what="lumogis-mock-capability")

    with httpx.Client(timeout=30.0) as c:
        cap = c.get(f"{base}/capabilities")
        assert cap.status_code == 200
        body = cap.json()
        assert body.get("id") == "lumogis.mock.echo"
        assert body.get("contract_version") == CONTRACT_VERSION
        names = {t.get("name") for t in body.get("tools") or []}
        assert TOOL_NAME in names
        ping = next((t for t in body.get("tools") or [] if t.get("name") == TOOL_NAME), None)
        assert ping is not None
        assert isinstance(ping.get("description"), str)
        assert (ping.get("invoke") or {}).get("path") == f"/tools/{TOOL_NAME}"

        echo = c.post(
            f"{base}/tools/{TOOL_NAME}",
            json=_mock_envelope({"rc": "ping"}),
            headers={"Authorization": f"Bearer {secret}"},
        )
        assert echo.status_code == 200
        payload = echo.json()
        assert payload.get("ok") is True
        assert payload.get("output") == {"echo": {"rc": "ping"}}

        missing = c.post(
            f"{base}/tools/mock.tool_does_not_exist",
            json=_mock_envelope({}),
            headers={"Authorization": f"Bearer {secret}"},
        )
        assert missing.status_code == 404

        bad_secret = c.post(
            f"{base}/tools/{TOOL_NAME}",
            json=_mock_envelope({}),
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
