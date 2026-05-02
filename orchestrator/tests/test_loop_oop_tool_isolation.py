# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Phase 3B closeout: OOP route ContextVar isolation, run_tool gating, stream cleanup."""

from __future__ import annotations

import json
import os
import threading
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
from services.unified_tools import OOP_TOOL_ROUTES
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


def _manifest(service_id: str, tools: list[CapabilityTool]) -> CapabilityManifest:
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


def _rsvc(mid: str, *tool_names: str, healthy: bool = True) -> RegisteredService:
    tools = [_ct(n) for n in tool_names]
    return RegisteredService(
        manifest=_manifest(mid, tools),
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


def _httpx_capture(monkeypatch: pytest.MonkeyPatch) -> list[httpx.Request]:
    captured: list[httpx.Request] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, text="{}")

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


def test_no_prepare_means_no_oop_dispatch_unknown_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Flag on but no request-scoped route → run_tool must not hit capability HTTP."""
    from services import tools

    cap = _httpx_capture(monkeypatch)
    monkeypatch.setenv("LUMOGIS_TOOL_CATALOG_ENABLED", "true")
    out = tools.run_tool("oop.only.without.prepare", {}, user_id="u1")
    d = json.loads(out)
    assert d.get("error", "").startswith("Unknown tool")
    assert cap == []
    assert OOP_TOOL_ROUTES.get() is None


def test_finish_clears_routes_try_run_stops_dispatching(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LUMOGIS_TOOL_CATALOG_ENABLED", "true")
    monkeypatch.setenv("LUMOGIS_CAPABILITY_BEARER_SVC_Z_FIRST", "tok")
    reg = _Reg(_rsvc("svc.z.first", "oop.one", healthy=True))
    monkeypatch.setattr("config.get_capability_registry", lambda: reg)

    assert OOP_TOOL_ROUTES.get() is None
    _, tok = prepare_llm_tools_for_request("user-a", capability_registry=reg)
    assert tok is not None
    assert "oop.one" in (OOP_TOOL_ROUTES.get() or {})

    finish_llm_tools_request(tok)
    assert OOP_TOOL_ROUTES.get() is None

    assert try_run_oop_capability_tool("oop.one", {}, user_id="user-a") is None


def test_two_threads_isolated_oop_route_maps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LUMOGIS_TOOL_CATALOG_ENABLED", "true")
    monkeypatch.setenv("LUMOGIS_CAPABILITY_BEARER_A_SVC", "ta")
    monkeypatch.setenv("LUMOGIS_CAPABILITY_BEARER_B_SVC", "tb")
    reg_a = _Reg(_rsvc("a.svc", "tool.a", healthy=True))
    reg_b = _Reg(_rsvc("b.svc", "tool.b", healthy=True))
    barrier = threading.Barrier(2)
    results: dict[str, set[str]] = {}
    tokens: dict[str, object] = {}

    def worker(key: str, reg: _Reg) -> None:
        _, tok = prepare_llm_tools_for_request(f"user-{key}", capability_registry=reg)
        assert tok is not None
        tokens[key] = tok
        barrier.wait()
        results[key] = set((OOP_TOOL_ROUTES.get() or {}).keys())
        barrier.wait()
        finish_llm_tools_request(tok)

    t1 = threading.Thread(target=worker, args=("1", reg_a))
    t2 = threading.Thread(target=worker, args=("2", reg_b))
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    assert results["1"] == {"tool.a"}
    assert results["2"] == {"tool.b"}


def test_prepared_context_unknown_oop_name_no_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from services import tools

    cap = _httpx_capture(monkeypatch)
    monkeypatch.setenv("LUMOGIS_TOOL_CATALOG_ENABLED", "true")
    monkeypatch.setenv("LUMOGIS_CAPABILITY_BEARER_SVC_Z_FIRST", "tok")
    reg = _Reg(_rsvc("svc.z.first", "registered.only", healthy=True))
    oop_tok = prepare_llm_tools_for_request("u", capability_registry=reg)[1]
    try:
        out = tools.run_tool("not.in.route.map", {}, user_id="u")
        d = json.loads(out)
        assert d.get("error", "").startswith("Unknown tool")
        assert cap == []
    finally:
        if oop_tok is not None:
            finish_llm_tools_request(oop_tok)


def test_inprocess_tool_spec_wins_name_collision_no_oop_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Core TOOL_SPECS name must not get a parallel OOP route or OOP dispatch."""
    from services import tools as tools_mod

    monkeypatch.setenv("LUMOGIS_TOOL_CATALOG_ENABLED", "true")
    monkeypatch.setenv("LUMOGIS_CAPABILITY_BEARER_COLL_SVC", "tok")
    reg = _Reg(
        _rsvc("coll.svc", "read_file", "extra.oop.coll", healthy=True),
    )
    merged, oop_tok = prepare_llm_tools_for_request("u", capability_registry=reg)
    try:
        extra_names = [d["name"] for d in merged[len(tools_mod.TOOLS) :]]
        assert "read_file" not in (OOP_TOOL_ROUTES.get() or {})
        assert "extra.oop.coll" in (OOP_TOOL_ROUTES.get() or {})
        assert extra_names == ["extra.oop.coll"]
        assert try_run_oop_capability_tool("read_file", {}, user_id="u") is None
    finally:
        if oop_tok is not None:
            finish_llm_tools_request(oop_tok)


def test_ask_stream_finishes_oop_on_provider_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``finally`` in ask_stream clears ContextVar even when streaming aborts early."""
    from loop import ask_stream

    import config

    monkeypatch.setenv("LUMOGIS_TOOL_CATALOG_ENABLED", "true")
    monkeypatch.setenv("LUMOGIS_CAPABILITY_BEARER_SVC_Z_FIRST", "tok")
    reg = _Reg(_rsvc("svc.z.first", "oop.stream", healthy=True))
    monkeypatch.setattr(config, "get_capability_registry", lambda: reg)

    def _boom(*_a, **_k):
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(config, "get_llm_provider", _boom)

    events = list(ask_stream("hello", user_id="u-stream", use_tools=True))
    assert any(e.type == "error" for e in events)
    assert OOP_TOOL_ROUTES.get() is None
