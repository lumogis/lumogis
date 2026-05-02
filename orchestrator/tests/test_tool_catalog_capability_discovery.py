# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Out-of-process capability tools appear as ``catalog_only`` (not executable
through Core's ``run_tool`` in Phase 2).
"""

from __future__ import annotations

import httpx
from models.capability import CapabilityLicenseMode
from models.capability import CapabilityManifest
from models.capability import CapabilityMaturity
from models.capability import CapabilityTool
from models.capability import CapabilityTransport
from services.capability_registry import CapabilityRegistry
from services.unified_tools import build_tool_catalog

from services import tools as services_tools


def _tool(name: str = "pro.discovered.api") -> CapabilityTool:
    return CapabilityTool(
        name=name,
        description="Discovered OOP tool",
        license_mode=CapabilityLicenseMode.COMMUNITY,
        input_schema={"type": "object"},
        output_schema={"type": "object"},
    )


def _manifest(
    service_id: str = "com.example.svc",
    tools: list[CapabilityTool] | None = None,
) -> CapabilityManifest:
    return CapabilityManifest(
        name=service_id,
        id=service_id,
        version="0.1.0",
        type="service",
        transport=CapabilityTransport.HTTP,
        license_mode=CapabilityLicenseMode.COMMUNITY,
        maturity=CapabilityMaturity.PREVIEW,
        description="Test capability",
        tools=tools if tools is not None else [_tool()],
        health_endpoint="/health",
        capabilities_endpoint="/capabilities",
        permissions_required=[],
        config_schema={"type": "object"},
        min_core_version="0.1.0",
        maintainer="test",
    )


def _manifest_handler(m: CapabilityManifest):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/capabilities":
            return httpx.Response(200, content=m.model_dump_json())
        return httpx.Response(404)

    return handler


async def test_capability_tool_rows_are_catalog_only() -> None:
    m = _manifest("com.example.catalogtest", tools=[_tool("custom.tool")])
    transport = httpx.MockTransport(_manifest_handler(m))
    reg = CapabilityRegistry(transport=transport)
    await reg.discover(["http://example-cap:9"])

    cat = build_tool_catalog(
        tool_specs=list(services_tools.TOOL_SPECS),
        list_actions_fn=lambda: [],
        capability_registry=reg,
    )
    rows = [e for e in cat.entries if e.name == "custom.tool" and e.source == "capability"]
    assert len(rows) == 1
    e = rows[0]
    assert e.transport == "catalog_only"
    assert e.origin_tier == "capability_backed"
    assert e.capability_id == "com.example.catalogtest"
    assert e.connector == "capability.com.example.catalogtest"
    assert e.action_type == "custom.tool"
    # Soft availability follows registry health (false until a successful probe).
    assert e.available is False
    assert e.why_not_available
