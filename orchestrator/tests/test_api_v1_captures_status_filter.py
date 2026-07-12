# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""GET /api/v1/captures ?status filter (LUM-606 — capture inbox).

Runs against the in-memory ``CapturesMemoryMetadataStore`` (via the shared
``client`` / ``captures_ms`` fixtures in ``test_api_v1_captures``). Asserts
filtered *behaviour* and a correct filtered ``total`` — the store's list and
COUNT handlers parse the new ``AND status = ANY(%s)`` clause by structure.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.captures_memory_store import CapturesMemoryMetadataStore

# Reuse the capture route fixtures.
from tests.test_api_v1_captures import capture_media_root  # noqa: F401
from tests.test_api_v1_captures import captures_ms  # noqa: F401
from tests.test_api_v1_captures import client  # noqa: F401


def _seed_three(client: TestClient, captures_ms: CapturesMemoryMetadataStore) -> dict[str, str]:
    """Create three captures and set them pending / failed / indexed."""
    ids: dict[str, str] = {}
    for status_name in ("pending", "failed", "indexed"):
        cid = client.post("/api/v1/captures", json={"text": f"note {status_name}"}).json()[
            "capture_id"
        ]
        captures_ms.captures[cid]["status"] = status_name
        if status_name == "failed":
            captures_ms.captures[cid]["last_error"] = "index_memory_unavailable"
        ids[status_name] = cid
    return ids


def test_list_captures_status_filter_pending_failed(
    client: TestClient, captures_ms: CapturesMemoryMetadataStore
):
    ids = _seed_three(client, captures_ms)
    r = client.get("/api/v1/captures?status=pending&status=failed")
    assert r.status_code == 200
    body = r.json()
    returned = {c["id"] for c in body["captures"]}
    assert returned == {ids["pending"], ids["failed"]}
    assert ids["indexed"] not in returned
    assert body["total"] == 2  # count is filtered, not the full set
    # updated_at DESC ordering preserved
    updated = [c["updated_at"] for c in body["captures"]]
    assert updated == sorted(updated, reverse=True)
    # last_error surfaced on the failed row straight off the list payload
    failed_row = next(c for c in body["captures"] if c["id"] == ids["failed"])
    assert failed_row["last_error"] == "index_memory_unavailable"


def test_list_captures_status_none_returns_all(
    client: TestClient, captures_ms: CapturesMemoryMetadataStore
):
    _seed_three(client, captures_ms)
    body = client.get("/api/v1/captures").json()
    assert body["total"] == 3
    assert {c["status"] for c in body["captures"]} == {"pending", "failed", "indexed"}


def test_list_captures_status_indexed_only(
    client: TestClient, captures_ms: CapturesMemoryMetadataStore
):
    # This is the LUM-607 archive path — prove the seam now.
    ids = _seed_three(client, captures_ms)
    body = client.get("/api/v1/captures?status=indexed").json()
    assert {c["id"] for c in body["captures"]} == {ids["indexed"]}
    assert body["total"] == 1


def test_list_captures_invalid_status_422(client: TestClient):
    assert client.get("/api/v1/captures?status=bogus").status_code == 422
