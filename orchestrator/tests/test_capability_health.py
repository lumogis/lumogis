# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Tests for Area 3 — out-of-process capability service health checks.

Covers:
  * RegisteredService.check_health (happy path, failure modes)
  * CapabilityRegistry.check_all_health[_sync] (parallel probes, never raise)
  * GET / status_page now exposes a `capability_services` section without
    breaking any pre-existing field
  * GET /health gains a minimal `capability_services` summary without
    changing its 200/503 behaviour or any pre-existing field
"""

from datetime import datetime
from datetime import timezone

import httpx
import pytest
from fastapi.testclient import TestClient
from models.capability import CapabilityLicenseMode
from models.capability import CapabilityManifest
from models.capability import CapabilityMaturity
from models.capability import CapabilityTransport
from services.capability_registry import CapabilityRegistry
from services.capability_registry import RegisteredService

import config as _config

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _manifest(
    service_id: str = "lumogis.memory.pro",
    health_endpoint: str = "/health",
    tools_count: int = 2,
) -> CapabilityManifest:
    from models.capability import CapabilityTool

    tools = [
        CapabilityTool(
            name=f"tool.{i}",
            description="x",
            license_mode=CapabilityLicenseMode.COMMUNITY,
            input_schema={"type": "object"},
            output_schema={"type": "object"},
        )
        for i in range(tools_count)
    ]
    return CapabilityManifest(
        name=service_id,
        id=service_id,
        version="0.1.0",
        type="service",
        transport=CapabilityTransport.HTTP,
        license_mode=CapabilityLicenseMode.COMMERCIAL,
        maturity=CapabilityMaturity.PREVIEW,
        description=f"{service_id} fixture",
        tools=tools,
        health_endpoint=health_endpoint,
        capabilities_endpoint="/capabilities",
        permissions_required=[],
        config_schema={"type": "object"},
        min_core_version="0.1.0",
        maintainer="lumogis",
    )


def _service(
    base_url: str = "http://memory-pro:8001",
    manifest: CapabilityManifest | None = None,
) -> RegisteredService:
    return RegisteredService(
        manifest=manifest or _manifest(),
        base_url=base_url,
        registered_at=datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# RegisteredService.check_health
# ---------------------------------------------------------------------------


async def test_check_health_200_marks_healthy_and_sets_timestamp():
    svc = _service()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/health"
        return httpx.Response(200, content=b"ok")

    transport = httpx.MockTransport(handler)

    assert svc.healthy is False
    assert svc.last_seen_healthy is None

    result = await svc.check_health(transport=transport)

    assert result is True
    assert svc.healthy is True
    assert svc.last_seen_healthy is not None
    assert svc.last_seen_healthy.tzinfo is not None  # timezone-aware


async def test_check_health_non_200_marks_unhealthy_preserves_timestamp():
    svc = _service()

    def ok(request):
        return httpx.Response(200)

    await svc.check_health(transport=httpx.MockTransport(ok))
    first_seen = svc.last_seen_healthy
    assert first_seen is not None

    def fail(request):
        return httpx.Response(503)

    result = await svc.check_health(transport=httpx.MockTransport(fail))

    assert result is False
    assert svc.healthy is False
    # last_seen_healthy preserved across failure
    assert svc.last_seen_healthy == first_seen


async def test_check_health_connection_error_marks_unhealthy():
    svc = _service()

    def boom(request):
        raise httpx.ConnectError("nope", request=request)

    result = await svc.check_health(transport=httpx.MockTransport(boom))

    assert result is False
    assert svc.healthy is False
    assert svc.last_seen_healthy is None


async def test_check_health_uses_manifest_health_endpoint():
    svc = _service(manifest=_manifest(health_endpoint="/internal/healthz"))
    seen_paths: list[str] = []

    def handler(request):
        seen_paths.append(request.url.path)
        return httpx.Response(200)

    await svc.check_health(transport=httpx.MockTransport(handler))
    assert seen_paths == ["/internal/healthz"]


# ---------------------------------------------------------------------------
# CapabilityRegistry.check_all_health
# ---------------------------------------------------------------------------


async def test_check_all_health_probes_every_registered_service():
    """End-to-end: discover two services, then probe both via check_all_health.

    Uses a single MockTransport that handles both /capabilities (for discovery)
    and /health (for the probe), keyed on the request host."""

    a_manifest = _manifest("svc.a", health_endpoint="/health")
    b_manifest = _manifest("svc.b", health_endpoint="/health")

    def handler(request: httpx.Request) -> httpx.Response:
        host = request.url.host
        if request.url.path == "/capabilities":
            m = a_manifest if host == "a" else b_manifest
            return httpx.Response(200, content=m.model_dump_json())
        if request.url.path == "/health":
            # svc.a healthy, svc.b unhealthy
            return httpx.Response(200 if host == "a" else 500)
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    registry = CapabilityRegistry(transport=transport)
    await registry.discover(["http://a", "http://b"])
    assert len(registry.all_services()) == 2

    await registry.check_all_health()

    by_id = {s.manifest.id: s for s in registry.all_services()}
    assert by_id["svc.a"].healthy is True
    assert by_id["svc.a"].last_seen_healthy is not None
    assert by_id["svc.b"].healthy is False
    assert by_id["svc.b"].last_seen_healthy is None


async def test_check_all_health_with_empty_registry_is_noop():
    registry = CapabilityRegistry()
    await registry.check_all_health()  # must not raise


def test_check_all_health_sync_runs_async_to_completion():
    a_manifest = _manifest("svc.sync", health_endpoint="/health")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/capabilities":
            return httpx.Response(200, content=a_manifest.model_dump_json())
        if request.url.path == "/health":
            return httpx.Response(200)
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    registry = CapabilityRegistry(transport=transport)
    registry.discover_sync(["http://sync"])
    registry.check_all_health_sync()

    assert registry.all_services()[0].healthy is True


def test_check_all_health_sync_swallows_exceptions(caplog, monkeypatch):
    import logging

    registry = CapabilityRegistry()

    async def boom():
        raise RuntimeError("synthetic")

    monkeypatch.setattr(registry, "check_all_health", boom)
    with caplog.at_level(logging.ERROR, logger="services.capability_registry"):
        registry.check_all_health_sync()
    assert any("health refresh failed" in rec.message for rec in caplog.records)


# ---------------------------------------------------------------------------
# GET /  (status_page) — capability_services section + regression
# ---------------------------------------------------------------------------


def _seed_registry_with(*services: RegisteredService) -> CapabilityRegistry:
    reg = CapabilityRegistry()
    for s in services:
        reg._services[s.manifest.id] = s
    _config._instances["capability_registry"] = reg
    return reg


def test_status_page_capability_services_empty_when_none_registered():
    _seed_registry_with()
    import main

    with TestClient(main.app) as client:
        resp = client.get("/")
    assert resp.status_code == 200
    body = resp.json()
    assert body["capability_services"] == {}
    # Pre-existing fields unchanged.
    for required in (
        "status",
        "embedding_model_ready",
        "documents_indexed",
        "sessions_stored",
        "entities_known",
        "services",
        "links",
        "setup_needed",
    ):
        assert required in body, f"missing pre-existing field: {required}"


def test_status_page_renders_registered_services_with_full_metadata():
    svc = _service(manifest=_manifest("lumogis.x.pro", tools_count=3))
    svc.healthy = True
    fixed_now = datetime(2026, 4, 17, 10, 0, 0, tzinfo=timezone.utc)
    svc.last_seen_healthy = fixed_now
    _seed_registry_with(svc)

    import main

    with TestClient(main.app) as client:
        resp = client.get("/")
    assert resp.status_code == 200
    body = resp.json()
    assert "lumogis.x.pro" in body["capability_services"]
    entry = body["capability_services"]["lumogis.x.pro"]
    assert entry == {
        "healthy": True,
        "version": "0.1.0",
        "tools_available": 3,
        "last_seen_healthy": fixed_now.isoformat(),
    }


def test_status_page_unhealthy_capability_does_not_degrade_core_status():
    """A capability service being down must NOT flip Core's status field
    to 'degraded' — that field is reserved for core service ping failures.
    """
    svc = _service()
    svc.healthy = False
    _seed_registry_with(svc)

    import main

    with TestClient(main.app) as client:
        resp = client.get("/")
    body = resp.json()
    assert body["capability_services"][svc.manifest.id]["healthy"] is False
    # Core mocks all pass ping(), so status must stay 'healthy'.
    assert body["status"] == "healthy"


# ---------------------------------------------------------------------------
# GET /health — minimal capability summary + regression
# ---------------------------------------------------------------------------


def test_health_endpoint_minimal_capability_summary_when_empty():
    _seed_registry_with()
    import main

    with TestClient(main.app) as client:
        resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["capability_services"] == {"registered": 0, "healthy": 0}
    # Pre-existing fields unchanged.
    for required in (
        "qdrant_doc_count",
        "file_index_count",
        "total_chunks_indexed",
        "entity_count",
        "last_ingest",
        "error_count",
        "chunk_drift_pct",
        "postgres_ok",
    ):
        assert required in body, f"missing pre-existing field: {required}"


def test_health_endpoint_summary_counts_healthy_services():
    healthy_a = _service(manifest=_manifest("svc.a"))
    healthy_a.healthy = True
    healthy_b = _service(manifest=_manifest("svc.b"))
    healthy_b.healthy = True
    sick = _service(manifest=_manifest("svc.c"))
    sick.healthy = False
    _seed_registry_with(healthy_a, healthy_b, sick)

    import main

    with TestClient(main.app) as client:
        resp = client.get("/health")
    body = resp.json()
    assert body["capability_services"] == {"registered": 3, "healthy": 2}


def test_health_endpoint_status_code_unchanged_by_capability_failures():
    """Capability service failures must not affect the 200/503 logic."""
    sick = _service()
    sick.healthy = False
    _seed_registry_with(sick)

    import main

    with TestClient(main.app) as client:
        resp = client.get("/health")
    # The conftest mock metadata store returns ping=True so we expect 200
    # regardless of capability service health.
    assert resp.status_code == 200


# Quiet the unused-import linter — pytest is needed for caplog/monkeypatch.
_ = pytest
