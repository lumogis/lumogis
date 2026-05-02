# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Phase 3B: ``try_run_oop_capability_tool`` + ``run_tool`` bridge for OOP names."""

from __future__ import annotations

import json
import os
from datetime import datetime
from datetime import timezone

import httpx
import pytest
from models.capability import CapabilityLicenseMode
from models.capability import CapabilityManifest
from models.capability import CapabilityMaturity
from models.capability import CapabilityTool
from models.capability import CapabilityTransport
from services.capability_registry import RegisteredService
from services.unified_tools import finish_llm_tools_request
from services.unified_tools import prepare_llm_tools_for_request
from services.unified_tools import try_run_oop_capability_tool


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
        permissions_required=[],
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


def _httpx_capturing(
    monkeypatch: pytest.MonkeyPatch, want_status: int = 200
) -> list[httpx.Request]:
    captured: list[httpx.Request] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        body = '{"echo":1}'
        if want_status == 200:
            return httpx.Response(200, text=body)
        return httpx.Response(want_status, text="down")

    transport = httpx.MockTransport(_handler)

    class _P(httpx.Client):
        def __init__(self, *a, **kwargs):
            kwargs.pop("transport", None)
            super().__init__(*a, transport=transport, **kwargs)

    monkeypatch.setattr(httpx, "Client", _P)
    return captured


@pytest.fixture(autouse=True)
def _clear_bearer_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for k in list(os.environ.keys()):
        if k.startswith("LUMOGIS_CAPABILITY_BEARER_"):
            monkeypatch.delenv(k, raising=False)


def test_oop_run_success_headers_user(monkeypatch: pytest.MonkeyPatch) -> None:
    from services import tools

    cap = _httpx_capturing(monkeypatch, 200)
    monkeypatch.setenv("LUMOGIS_TOOL_CATALOG_ENABLED", "true")
    tname = "oop.cap.tool"
    monkeypatch.setenv("LUMOGIS_CAPABILITY_BEARER_SVC_Z_FIRST", "tok-9")
    reg = _Reg(_rsvc("svc.z.first", tname, healthy=True))
    _tools_list, oop_tok = prepare_llm_tools_for_request("user-x", capability_registry=reg)
    try:
        monkeypatch.setattr("config.get_capability_registry", lambda: reg)
        monkeypatch.setattr("permissions.check_permission", lambda *a, **k: True)
        out = tools.run_tool(tname, {"q": 1}, user_id="user-x")
    finally:
        if oop_tok is not None:
            finish_llm_tools_request(oop_tok)

    assert cap
    r0 = cap[0]
    assert r0.url == httpx.URL("http://cap.test:9/tools/oop.cap.tool")
    assert r0.headers.get("x-lumogis-user") == "user-x"
    assert "Bearer tok-9" in (r0.headers.get("authorization") or "")
    assert "echo" in out


def test_flag_off_try_run_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LUMOGIS_TOOL_CATALOG_ENABLED", "false")
    out = try_run_oop_capability_tool("anything", {}, user_id="u")
    assert out is None


def test_unhealthy_at_dispatch_fails_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LUMOGIS_TOOL_CATALOG_ENABLED", "true")
    monkeypatch.setenv("LUMOGIS_CAPABILITY_BEARER_SVC_Z_FIRST", "tok")
    rsvc = _rsvc("svc.z.first", "oop.x", healthy=True)
    reg = _Reg(rsvc)
    oop_tok = prepare_llm_tools_for_request("u", capability_registry=reg)[1]
    try:
        rsvc.healthy = False
        monkeypatch.setattr("config.get_capability_registry", lambda: reg)
        out = try_run_oop_capability_tool("oop.x", {}, user_id="u")
        err = json.loads(out or "")
        assert err.get("error") == "capability service unavailable"
    finally:
        if oop_tok is not None:
            finish_llm_tools_request(oop_tok)


def test_permission_denies_no_http(monkeypatch: pytest.MonkeyPatch) -> None:
    from services import tools

    cap = _httpx_capturing(monkeypatch, 200)
    monkeypatch.setenv("LUMOGIS_TOOL_CATALOG_ENABLED", "true")
    monkeypatch.setenv("LUMOGIS_CAPABILITY_BEARER_SVC_Z_FIRST", "tok")
    reg = _Reg(_rsvc("svc.z.first", "deny.me", healthy=True))
    oop_tok = prepare_llm_tools_for_request("u", capability_registry=reg)[1]
    try:
        monkeypatch.setattr("config.get_capability_registry", lambda: reg)
        monkeypatch.setattr("permissions.check_permission", lambda *a, **k: False)
        out = tools.run_tool("deny.me", {}, user_id="u")
        d = json.loads(out)
        assert d.get("error") == "Permission denied"
        assert cap == []
    finally:
        if oop_tok is not None:
            finish_llm_tools_request(oop_tok)


def test_run_tool_unknown_still_error(monkeypatch: pytest.MonkeyPatch) -> None:
    from services import tools

    monkeypatch.setenv("LUMOGIS_TOOL_CATALOG_ENABLED", "true")
    out = tools.run_tool("nope_nope_zzz", {}, user_id="u")
    d = json.loads(out)
    assert d.get("error", "").startswith("Unknown tool")
