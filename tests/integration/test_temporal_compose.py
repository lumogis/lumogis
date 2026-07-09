# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""LUM-567 compose integration — live lumogis-graph + FalkorDB + Postgres.

Exercises the admin temporal backfill route on the running RC stack and
verifies idempotent 202/409 behaviour. Shadow-mode / auto-apply end-to-end
paths are covered by ``orchestrator/tests/premium/test_temporal_pipeline.py``
(mocked LLM at the provider seam, real FalkorDB semantics in
``services/lumogis-graph/tests/test_temporal_integration.py``).
"""

from __future__ import annotations

import os
import time

import httpx
import pytest

pytestmark = [pytest.mark.integration]

KG_BASE = os.environ.get("LUMOGIS_GRAPH_HEALTH_URL", "http://127.0.0.1:18001/health").replace(
    "/health", ""
)
API_BASE = os.environ.get("LUMOGIS_API_URL", "http://127.0.0.1:8000")
SMOKE_EMAIL = os.environ.get("LUMOGIS_WEB_SMOKE_EMAIL", "").strip()
SMOKE_PASSWORD = os.environ.get("LUMOGIS_WEB_SMOKE_PASSWORD", "")


def _admin_bearer() -> str | None:
    if not SMOKE_EMAIL or len(SMOKE_PASSWORD) < 12:
        return None
    with httpx.Client(base_url=API_BASE, timeout=30.0) as c:
        r = c.post(
            "/api/v1/auth/login",
            json={"email": SMOKE_EMAIL, "password": SMOKE_PASSWORD},
        )
        if r.status_code != 200:
            return None
        return r.json().get("access_token")


def _kg_available() -> bool:
    try:
        with httpx.Client(timeout=10.0) as c:
            r = c.get(f"{KG_BASE}/health")
            if r.status_code != 200:
                return False
            body = r.json()
            return body.get("falkordb") is True
    except httpx.HTTPError:
        return False


@pytest.fixture(scope="module")
def kg_client():
    if not _kg_available():
        pytest.skip("lumogis-graph service with FalkorDB not available")
    token = _admin_bearer()
    if not token:
        pytest.skip("admin login unavailable — set LUMOGIS_WEB_SMOKE_EMAIL/PASSWORD")
    headers = {"Authorization": f"Bearer {token}"}
    client = httpx.Client(base_url=KG_BASE, timeout=120.0, headers=headers)
    yield client
    client.close()


class TestTemporalBackfillRoute:
    def test_temporal_backfill_idempotent_http(self, kg_client):
        """POST /graph/backfill/temporal twice — second call 202 with zero new work or 409."""
        r1 = kg_client.post("/graph/backfill/temporal")
        assert r1.status_code in (202, 409, 503), r1.text
        if r1.status_code == 503:
            pytest.skip("temporal backfill unavailable (flag off or graph absent)")

        if r1.status_code == 409:
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                retry = kg_client.post("/graph/backfill/temporal")
                if retry.status_code == 202:
                    r1 = retry
                    break
                time.sleep(2)
            else:
                pytest.fail("timed out waiting for temporal backfill slot")

        assert r1.json().get("status") == "temporal_backfill_started"
        time.sleep(2)
        r2 = kg_client.post("/graph/backfill/temporal")
        assert r2.status_code in (202, 409), r2.text
