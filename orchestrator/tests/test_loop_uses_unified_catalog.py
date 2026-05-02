# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Phase 3B: ``prepare_llm_tools_for_request`` identity vs base ``TOOLS`` (flag + registry)."""

from __future__ import annotations

import os
from datetime import datetime
from datetime import timezone

import pytest
from models.capability import CapabilityLicenseMode
from models.capability import CapabilityManifest
from models.capability import CapabilityMaturity
from models.capability import CapabilityTool
from models.capability import CapabilityTransport
from services.capability_registry import RegisteredService
from services.unified_tools import finish_llm_tools_request
from services.unified_tools import prepare_llm_tools_for_request

from services import tools as tools_mod


def _ct(name: str) -> CapabilityTool:
    return CapabilityTool(
        name=name,
        description="OOP",
        license_mode=CapabilityLicenseMode.COMMUNITY,
        input_schema={"type": "object"},
        output_schema={"type": "object"},
    )


def _manifest(
    service_id: str,
    tools: list[CapabilityTool],
    *,
    permissions: list[str] | None = None,
) -> CapabilityManifest:
    return CapabilityManifest(
        name=service_id,
        id=service_id,
        version="0.1.0",
        type="service",
        transport=CapabilityTransport.HTTP,
        license_mode=CapabilityLicenseMode.COMMUNITY,
        maturity=CapabilityMaturity.PREVIEW,
        description="Test",
        tools=tools,
        health_endpoint="/health",
        capabilities_endpoint="/capabilities",
        permissions_required=permissions or [],
        config_schema={"type": "object"},
        min_core_version="0.1.0",
        maintainer="t",
    )


def _rsvc(mid: str, tool: str, *, healthy: bool = True) -> RegisteredService:
    return RegisteredService(
        manifest=_manifest(mid, [_ct(tool)]),
        base_url="http://cap.test:9",
        registered_at=datetime.now(timezone.utc),
        healthy=healthy,
    )


class _Reg:
    def __init__(self, *regs: RegisteredService) -> None:
        self._m = {r.manifest.id: r for r in regs}

    def all_services(self) -> list[RegisteredService]:
        return list(self._m.values())

    def get_service(self, sid: str) -> RegisteredService | None:
        return self._m.get(sid)


@pytest.fixture(autouse=True)
def _clear_bearer_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for k in list(os.environ.keys()):
        if k.startswith("LUMOGIS_CAPABILITY_BEARER_"):
            monkeypatch.delenv(k, raising=False)


def test_flag_off_tools_is_byte_identical_globals(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LUMOGIS_TOOL_CATALOG_ENABLED", "false")
    tools, tok = prepare_llm_tools_for_request("user-1", capability_registry=_Reg())
    assert tools is tools_mod.TOOLS
    assert tok is None


def test_flag_on_no_eligible_oop_is_byte_identical(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LUMOGIS_TOOL_CATALOG_ENABLED", "true")
    # Unhealthy: nothing eligible.
    r = _Reg(_rsvc("a", "x.y", healthy=False))
    tools, tok = prepare_llm_tools_for_request("u1", capability_registry=r)
    assert tools is tools_mod.TOOLS
    assert tok is None

    # Healthy but no bearer: skip whole service, nothing to append.
    r = _rsvc("svc.z.first", "oop.cap.tool", healthy=True)
    monkeypatch.delenv("LUMOGIS_CAPABILITY_BEARER_SVC_Z_FIRST", raising=False)
    tools2, tok2 = prepare_llm_tools_for_request("u1", capability_registry=_Reg(r))
    assert tools2 is tools_mod.TOOLS
    assert tok2 is None


def test_flag_on_healthy_bearer_appends_one_tool_deterministically(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LUMOGIS_TOOL_CATALOG_ENABLED", "true")
    monkeypatch.setenv("LUMOGIS_CAPABILITY_BEARER_SVC_Z_FIRST", "sec-token")
    b = tools_mod.TOOLS
    r0 = _rsvc("svc.z.first", "oop.cap.tool", healthy=True)
    r1 = _rsvc("other.svc", "other.tool", healthy=True)
    # Only first has bearer; second skipped so we get exactly one extra def.
    monkeypatch.setenv("LUMOGIS_CAPABILITY_BEARER_OTHER_SVC", "")
    reg = _Reg(r1, r0)
    tools, tok = prepare_llm_tools_for_request("u1", capability_registry=reg)
    try:
        assert tools is not tools_mod.TOOLS
        assert len(tools) == len(b) + 1
        assert tools[: len(b)] == b
        assert tools[-1]["name"] == "oop.cap.tool"
    finally:
        if tok is not None:
            finish_llm_tools_request(tok)


def test_two_services_extras_sorted_by_service_id_then_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LUMOGIS_TOOL_CATALOG_ENABLED", "true")
    monkeypatch.setenv("LUMOGIS_CAPABILITY_BEARER_B_SVC", "b")
    monkeypatch.setenv("LUMOGIS_CAPABILITY_BEARER_A_SVC", "a")
    reg = _Reg(
        _rsvc("b.svc", "tool.b", healthy=True),
        _rsvc("a.svc", "tool.a", healthy=True),
    )
    tools, tok = prepare_llm_tools_for_request("u1", capability_registry=reg)
    try:
        ext = [d["name"] for d in tools[len(tools_mod.TOOLS) :]]
        assert ext == ["tool.a", "tool.b"]
    finally:
        if tok is not None:
            finish_llm_tools_request(tok)
