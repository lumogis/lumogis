# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Least-privilege scope enforcement at the capability chokepoint (LUM-612).

Exercises ``ToolExecutor.execute_capability_http``'s scope gate with an injected
permission check and a monkeypatched ``permissions.get_granted_scopes`` (no DB):
a required scope the user has not granted → fail-closed deny that NAMES the
missing scopes; a granted scope → proceed; empty ``required_scopes`` → no gate.
"""

from __future__ import annotations

import json

import httpx
import pytest
from services.execution import PermissionCheck
from services.execution import ToolExecutor


def _patch_httpx(monkeypatch, handler) -> None:
    transport = httpx.MockTransport(handler)
    real = httpx.Client

    class _P(real):
        def __init__(self, *a, **k):
            k.pop("transport", None)
            super().__init__(*a, transport=transport, **k)

    monkeypatch.setattr(httpx, "Client", _P)


def _executor(**pc):
    return ToolExecutor(permission=PermissionCheck(**pc), emit_audit=lambda _e: None)


def _call(ex, *, required_scopes, base_url="http://cap.test:1", handler_ran=None):
    return ex.execute_capability_http(
        user_id="u1",
        request_id="r1",
        tool_name="cap.tool",
        capability_id="cap-x",
        connector="capability.cap-x",
        action_type="cap.tool",
        is_write=True,
        base_url=base_url,
        input_={"a": 1},
        required_scopes=required_scopes,
        get_service_bearer=lambda: "sec",
    )


def test_denied_when_required_scope_ungranted_names_missing(monkeypatch):
    # binary Ask/Do allows; user has granted only memory:read.
    monkeypatch.setattr(
        "permissions.get_granted_scopes", lambda *, user_id, connector: ["memory:read"]
    )
    hits: list[str] = []

    def _h(_r):
        hits.append("http")
        return httpx.Response(200, json={"ok": True, "output": 1})

    _patch_httpx(monkeypatch, _h)
    ex = _executor(check=lambda *a, **k: True)
    res = _call(ex, required_scopes=["memory:write"])
    assert res.denied is True
    assert hits == [], "must fail closed BEFORE any HTTP"
    payload = json.loads(res.output)
    assert payload["missing_scopes"] == ["memory:write"]
    assert "capability.cap-x" in payload["hint"]


def test_allowed_when_required_scope_granted(monkeypatch):
    monkeypatch.setattr(
        "permissions.get_granted_scopes",
        lambda *, user_id, connector: ["memory:read", "memory:write"],
    )
    _patch_httpx(monkeypatch, lambda _r: httpx.Response(200, json={"ok": True, "output": "done"}))
    ex = _executor(check=lambda *a, **k: True)
    res = _call(ex, required_scopes=["memory:write"])
    assert res.success is True
    assert res.output == "done"


def test_empty_required_scopes_skips_the_gate(monkeypatch):
    called = {"n": 0}

    def _spy(*, user_id, connector):
        called["n"] += 1
        return []

    monkeypatch.setattr("permissions.get_granted_scopes", _spy)
    _patch_httpx(monkeypatch, lambda _r: httpx.Response(200, json={"ok": True, "output": "ok"}))
    ex = _executor(check=lambda *a, **k: True)
    res = _call(ex, required_scopes=[])
    assert res.success is True
    assert called["n"] == 0  # no scope lookup when nothing required (back-compat with []-manifests)


def test_scope_gate_never_relaxes_binary_deny(monkeypatch):
    # binary Ask/Do denies (is_write + ASK) → denied regardless of scopes.
    monkeypatch.setattr(
        "permissions.get_granted_scopes", lambda *, user_id, connector: ["memory:write"]
    )
    ex = _executor(check=lambda *a, **k: False)
    res = _call(ex, required_scopes=["memory:write"])
    assert res.denied is True


def test_required_scopes_is_a_required_kwarg():
    # A forgotten thread must be a TypeError, never a silent bypass.
    ex = _executor(check=lambda *a, **k: True)
    with pytest.raises(TypeError):
        ex.execute_capability_http(
            user_id="u",
            request_id="r",
            tool_name="t",
            capability_id="c",
            connector="capability.c",
            action_type="t",
            is_write=False,
            base_url="http://x",
            input_={},
            get_service_bearer=lambda: "s",
        )


def test_llm_denial_names_missing_scopes(monkeypatch):
    """LUM-615 — ``try_run_oop_capability_tool`` surfaces missing scopes, not generic deny."""
    from datetime import datetime
    from datetime import timezone

    from models.capability import CapabilityLicenseMode
    from models.capability import CapabilityManifest
    from models.capability import CapabilityMaturity
    from models.capability import CapabilityTool
    from models.capability import CapabilityTransport
    from services.capability_registry import RegisteredService
    from services.unified_tools import finish_llm_tools_request
    from services.unified_tools import prepare_llm_tools_for_request
    from services.unified_tools import try_run_oop_capability_tool

    tool_name = "scope.gated.tool"
    manifest = CapabilityManifest(
        name="scope-gated",
        id="scope-gated",
        version="1.0.0",
        type="service",
        transport=CapabilityTransport.HTTP,
        license_mode=CapabilityLicenseMode.COMMUNITY,
        maturity=CapabilityMaturity.PREVIEW,
        description="d",
        tools=[
            CapabilityTool(
                name=tool_name,
                description="t",
                license_mode=CapabilityLicenseMode.COMMUNITY,
                input_schema={"type": "object"},
                output_schema={"type": "object"},
            )
        ],
        health_endpoint="/health",
        capabilities_endpoint="/capabilities",
        permissions_required=["memory:write"],
        config_schema={"type": "object"},
        min_core_version="0.1.0",
        maintainer="t",
    )

    class _Reg:
        def all_services(self):
            return [
                RegisteredService(
                    manifest=manifest,
                    base_url="http://scope-cap:1",
                    registered_at=datetime.now(timezone.utc),
                    healthy=True,
                    is_community=False,
                )
            ]

        def get_service(self, capability_id: str):
            for svc in self.all_services():
                if svc.manifest.id == capability_id:
                    return svc
            return None

    reg = _Reg()
    monkeypatch.setenv("LUMOGIS_TOOL_CATALOG_ENABLED", "true")
    monkeypatch.setenv("LUMOGIS_CAPABILITY_BEARER_SCOPE_GATED", "tok")
    monkeypatch.setattr("config.get_capability_registry", lambda: reg)
    monkeypatch.setattr("permissions.check_permission", lambda *a, **k: True)
    monkeypatch.setattr(
        "permissions.get_granted_scopes", lambda *, user_id, connector: ["memory:read"]
    )

    http_hits: list[str] = []

    def _never(_r):
        http_hits.append("http")
        return httpx.Response(200, json={"ok": True})

    _patch_httpx(monkeypatch, _never)
    tok = prepare_llm_tools_for_request("u-scope", capability_registry=reg)[1]
    try:
        out = try_run_oop_capability_tool(tool_name, {}, user_id="u-scope")
    finally:
        if tok is not None:
            finish_llm_tools_request(tok)

    assert http_hits == []
    payload = json.loads(out)
    assert payload["missing_scopes"] == ["memory:write"]
    assert "Permission denied" not in out
