# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Lumogis
"""Tests for services/capability_registry.py and config helpers (Area 2).

Uses httpx.MockTransport (built into httpx — no new test dependency) to
drive the registry without real network calls. The CapabilityRegistry
exposes a `transport=` test seam that production code never sets.
"""

import logging

import httpx
import pytest

from models.capability import CapabilityLicenseMode
from models.capability import CapabilityManifest
from models.capability import CapabilityMaturity
from models.capability import CapabilityTool
from models.capability import CapabilityTransport
from services.capability_registry import CapabilityRegistry

import config as _config


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _tool(
    name: str = "memory.search",
    license_mode: CapabilityLicenseMode = CapabilityLicenseMode.COMMUNITY,
) -> CapabilityTool:
    return CapabilityTool(
        name=name,
        description=f"Tool {name}",
        license_mode=license_mode,
        input_schema={"type": "object"},
        output_schema={"type": "object"},
    )


def _manifest(
    service_id: str = "lumogis.memory.pro",
    version: str = "0.1.0",
    min_core_version: str = "0.1.0",
    tools: list[CapabilityTool] | None = None,
) -> CapabilityManifest:
    return CapabilityManifest(
        name=service_id,
        id=service_id,
        version=version,
        type="service",
        transport=CapabilityTransport.HTTP,
        license_mode=CapabilityLicenseMode.COMMERCIAL,
        maturity=CapabilityMaturity.PREVIEW,
        description=f"{service_id} test fixture",
        tools=tools if tools is not None else [_tool()],
        health_endpoint="/health",
        capabilities_endpoint="/capabilities",
        permissions_required=[],
        config_schema={"type": "object"},
        min_core_version=min_core_version,
        maintainer="lumogis",
    )


def _manifest_handler(manifest: CapabilityManifest):
    """Return an httpx MockTransport handler that serves the given manifest
    at /capabilities and 404s everything else."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/capabilities":
            return httpx.Response(200, content=manifest.model_dump_json())
        return httpx.Response(404)

    return handler


# ---------------------------------------------------------------------------
# discover() — happy path
# ---------------------------------------------------------------------------


async def test_discover_populates_registry_from_valid_manifest():
    manifest = _manifest(tools=[_tool("memory.search"), _tool("memory.recent")])
    transport = httpx.MockTransport(_manifest_handler(manifest))
    registry = CapabilityRegistry(transport=transport)

    await registry.discover(["http://memory-pro:8001"])

    services = registry.all_services()
    assert len(services) == 1
    svc = services[0]
    assert svc.manifest.id == "lumogis.memory.pro"
    assert svc.base_url == "http://memory-pro:8001"
    assert svc.registered_at is not None
    assert svc.last_seen_healthy is None
    assert svc.healthy is False

    tools = registry.get_tools()
    assert {t.name for t in tools} == {"memory.search", "memory.recent"}


async def test_discover_with_empty_url_list_is_noop():
    registry = CapabilityRegistry()
    await registry.discover([])
    assert registry.all_services() == []
    assert registry.get_tools() == []


# ---------------------------------------------------------------------------
# discover() — failure modes (must not raise)
# ---------------------------------------------------------------------------


async def test_discover_unreachable_service_is_warning_not_error(caplog):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    transport = httpx.MockTransport(handler)
    registry = CapabilityRegistry(transport=transport)

    with caplog.at_level(logging.WARNING, logger="services.capability_registry"):
        await registry.discover(["http://nope:9999"])

    assert registry.all_services() == []
    assert any("unreachable" in rec.message for rec in caplog.records)


async def test_discover_invalid_manifest_logs_warning_and_skips(caplog):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b'{"not": "a manifest"}')

    transport = httpx.MockTransport(handler)
    registry = CapabilityRegistry(transport=transport)

    with caplog.at_level(logging.WARNING, logger="services.capability_registry"):
        await registry.discover(["http://broken:8001"])

    assert registry.all_services() == []
    assert any("invalid manifest" in rec.message for rec in caplog.records)


async def test_discover_non_200_response_skips(caplog):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, content=b"server error")

    transport = httpx.MockTransport(handler)
    registry = CapabilityRegistry(transport=transport)

    with caplog.at_level(logging.WARNING, logger="services.capability_registry"):
        await registry.discover(["http://flaky:8001"])

    assert registry.all_services() == []
    assert any("HTTP 500" in rec.message for rec in caplog.records)


async def test_discover_incompatible_min_core_version_skips(caplog):
    manifest = _manifest(min_core_version="999.0.0")
    transport = httpx.MockTransport(_manifest_handler(manifest))
    registry = CapabilityRegistry(transport=transport)

    with caplog.at_level(logging.WARNING, logger="services.capability_registry"):
        await registry.discover(["http://future:8001"])

    assert registry.all_services() == []
    assert any("requires Core" in rec.message for rec in caplog.records)


# ---------------------------------------------------------------------------
# Refresh / upsert behaviour
# ---------------------------------------------------------------------------


async def test_discover_refresh_updates_in_place_no_duplicates():
    v1 = _manifest(version="0.1.0")
    transport_v1 = httpx.MockTransport(_manifest_handler(v1))
    registry = CapabilityRegistry(transport=transport_v1)
    await registry.discover(["http://memory-pro:8001"])

    first_registered_at = registry.all_services()[0].registered_at

    v2 = _manifest(version="0.2.0", tools=[_tool("memory.search"), _tool("memory.summarize")])
    transport_v2 = httpx.MockTransport(_manifest_handler(v2))
    registry._transport = transport_v2

    await registry.discover(["http://memory-pro:8001"])

    services = registry.all_services()
    assert len(services) == 1, "refresh must update in place, not duplicate"
    assert services[0].manifest.version == "0.2.0"
    assert services[0].registered_at == first_registered_at, (
        "registered_at must be preserved across refresh"
    )
    assert {t.name for t in services[0].manifest.tools} == {
        "memory.search",
        "memory.summarize",
    }


# ---------------------------------------------------------------------------
# get_tools() filtering
# ---------------------------------------------------------------------------


async def test_get_tools_filters_by_license_mode():
    manifest = _manifest(
        tools=[
            _tool("memory.search", CapabilityLicenseMode.COMMUNITY),
            _tool("memory.summarize", CapabilityLicenseMode.COMMERCIAL),
        ],
    )
    transport = httpx.MockTransport(_manifest_handler(manifest))
    registry = CapabilityRegistry(transport=transport)
    await registry.discover(["http://memory-pro:8001"])

    assert {t.name for t in registry.get_tools()} == {"memory.search", "memory.summarize"}
    assert [t.name for t in registry.get_tools(CapabilityLicenseMode.COMMUNITY)] == [
        "memory.search"
    ]
    assert [t.name for t in registry.get_tools(CapabilityLicenseMode.COMMERCIAL)] == [
        "memory.summarize"
    ]


def test_get_service_returns_none_for_unknown_id():
    registry = CapabilityRegistry()
    assert registry.get_service("nope") is None


# ---------------------------------------------------------------------------
# discover_sync — APScheduler-facing wrapper
# ---------------------------------------------------------------------------


def test_discover_sync_runs_async_discover_to_completion():
    manifest = _manifest()
    transport = httpx.MockTransport(_manifest_handler(manifest))
    registry = CapabilityRegistry(transport=transport)

    registry.discover_sync(["http://memory-pro:8001"])

    assert len(registry.all_services()) == 1


def test_discover_sync_swallows_exceptions(caplog, monkeypatch):
    """If the underlying discover() somehow raises (it shouldn't, but defence
    in depth), the sync wrapper must not propagate — the scheduled job
    cannot be allowed to crash the scheduler thread."""
    registry = CapabilityRegistry()

    async def boom(_urls):
        raise RuntimeError("synthetic")

    monkeypatch.setattr(registry, "discover", boom)
    with caplog.at_level(logging.ERROR, logger="services.capability_registry"):
        registry.discover_sync(["http://x"])
    assert any("refresh failed" in rec.message for rec in caplog.records)


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------


def test_get_capability_service_urls_parses_env_var(monkeypatch):
    monkeypatch.setenv("CAPABILITY_SERVICE_URLS", "http://a:1, http://b:2 ,, http://c:3")
    assert _config.get_capability_service_urls() == [
        "http://a:1",
        "http://b:2",
        "http://c:3",
    ]


def test_get_capability_service_urls_empty_when_unset(monkeypatch):
    monkeypatch.delenv("CAPABILITY_SERVICE_URLS", raising=False)
    assert _config.get_capability_service_urls() == []


def test_get_capability_service_urls_empty_when_blank(monkeypatch):
    monkeypatch.setenv("CAPABILITY_SERVICE_URLS", "   , ,  ")
    assert _config.get_capability_service_urls() == []


def test_get_capability_registry_is_singleton():
    # The autouse _override_config fixture in conftest clears _instances on
    # teardown, so two consecutive get_*() calls return the same object
    # within a single test.
    a = _config.get_capability_registry()
    b = _config.get_capability_registry()
    assert a is b
    assert isinstance(a, CapabilityRegistry)


# pytest-asyncio is configured with asyncio_mode = "auto" in pyproject.toml,
# so the `async def test_*` functions above are picked up automatically with
# no decorator or pytestmark. The `pytest` import is kept for `caplog` and
# `monkeypatch` fixtures.
_ = pytest  # silence unused-import linters if any kick in
