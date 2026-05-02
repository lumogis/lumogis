# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""lumogis-mock-capability manifest + Core unified catalog (RC compose)."""

from __future__ import annotations

import os

import httpx
import pytest

pytestmark = pytest.mark.integration


@pytest.mark.public_rc
def test_mock_capability_manifest_and_echo():
    base = os.environ.get("LUMOGIS_MOCK_CAPABILITY_BASE_URL", "http://127.0.0.1:18080").strip().rstrip("/")
    secret = os.environ.get(
        "MOCK_CAPABILITY_SHARED_SECRET",
        "rc-mock-cap-deterministic-secret-do-not-use-prod",
    ).strip()

    with httpx.Client(timeout=30.0) as c:
        cap = c.get(f"{base}/capabilities")
        assert cap.status_code == 200
        body = cap.json()
        assert body.get("id") == "lumogis.mock.echo"
        names = {t.get("name") for t in body.get("tools") or []}
        assert "mock.echo_ping" in names
        ping = next((t for t in body.get("tools") or [] if t.get("name") == "mock.echo_ping"), None)
        assert ping is not None
        assert isinstance(ping.get("description"), str)

        echo = c.post(
            f"{base}/tools/mock.echo_ping",
            json={"rc": "ping"},
            headers={"Authorization": f"Bearer {secret}"},
        )
        assert echo.status_code == 200
        assert echo.json().get("ok") is True

        missing = c.post(
            f"{base}/tools/mock.tool_does_not_exist",
            json={},
            headers={"Authorization": f"Bearer {secret}"},
        )
        assert missing.status_code == 404

        bad_secret = c.post(
            f"{base}/tools/mock.echo_ping",
            json={},
            headers={"Authorization": "Bearer definitely-not-the-rc-secret"},
        )
        assert bad_secret.status_code == 403


@pytest.mark.public_rc
def test_core_tool_catalog_includes_mock_echo(api):
    r = api.get("/api/v1/me/tools")
    assert r.status_code == 200
    tools = r.json().get("tools") or []
    names = {t.get("name") for t in tools}
    assert "mock.echo_ping" in names
    row = next((t for t in tools if t.get("name") == "mock.echo_ping"), None)
    assert row is not None
    assert isinstance(row.get("description"), str)
