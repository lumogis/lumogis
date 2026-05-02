# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Phase 5: generic capability contract — mock second service (tests only).

Proves a non-graph capability id + tool can use CapabilityRegistry, ToolCatalog,
me tools façade, admin diagnostics, and Phase 3B OOP dispatch without production
infrastructure. IDs: ``mock-echo`` / ``mock.echo_ping``.
"""

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
from services.admin_diagnostics import build_admin_diagnostics_response
from services.capability_http import post_capability_tool_invocation
from services.capability_registry import CapabilityRegistry
from services.capability_registry import RegisteredService
from services.execution import PermissionCheck
from services.execution import ToolAuditEnvelope
from services.execution import ToolExecutor
from services.me_tools_catalog import build_me_tools_response
from services.unified_tools import build_tool_catalog
from services.unified_tools import finish_llm_tools_request
from services.unified_tools import prepare_llm_tools_for_request
from services.unified_tools import try_run_oop_capability_tool

from services import tools as services_tools

MOCK_CAP_ID = "mock-echo"
MOCK_TOOL = "mock.echo_ping"
MOCK_BASE = "http://mock-echo.test:9"
BEARER_ENV_KEY = "LUMOGIS_CAPABILITY_BEARER_MOCK_ECHO"


def _echo_tool() -> CapabilityTool:
    return CapabilityTool(
        name=MOCK_TOOL,
        description="Mock echo ping for Phase 5 contract tests.",
        license_mode=CapabilityLicenseMode.COMMUNITY,
        input_schema={
            "type": "object",
            "properties": {"msg": {"type": "string"}},
        },
        output_schema={"type": "object"},
    )


def _mock_manifest() -> CapabilityManifest:
    return CapabilityManifest(
        name="Mock Echo Capability",
        id=MOCK_CAP_ID,
        version="0.0.1-mock",
        type="service",
        transport=CapabilityTransport.HTTP,
        license_mode=CapabilityLicenseMode.COMMUNITY,
        maturity=CapabilityMaturity.PREVIEW,
        description="Test-only mock capability (not a production service).",
        tools=[_echo_tool()],
        health_endpoint="/health",
        capabilities_endpoint="/capabilities",
        permissions_required=[],
        config_schema={"type": "object"},
        min_core_version="0.1.0",
        maintainer="lumogis-tests",
    )


def _mock_transport_handler(manifest: CapabilityManifest):
    """Serve /capabilities, /health, and POST /tools/mock.echo_ping."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/capabilities":
            return httpx.Response(200, content=manifest.model_dump_json())
        if path == "/health":
            return httpx.Response(200, json={"ok": True})
        if path == f"/tools/{MOCK_TOOL}":
            auth = request.headers.get("authorization") or ""
            if not auth.startswith("Bearer "):
                return httpx.Response(401, text="unauthorized")
            return httpx.Response(200, text='{"pong":true}')
        return httpx.Response(404, text="not found")

    return handler


def _registered_service(*, healthy: bool) -> RegisteredService:
    return RegisteredService(
        manifest=_mock_manifest(),
        base_url=MOCK_BASE,
        registered_at=datetime.now(timezone.utc),
        healthy=healthy,
    )


class _FakeRegistry:
    def __init__(self, *services: RegisteredService) -> None:
        self._by_id = {s.manifest.id: s for s in services}

    def all_services(self) -> list[RegisteredService]:
        return list(self._by_id.values())

    def get_service(self, sid: str) -> RegisteredService | None:
        return self._by_id.get(sid)


@pytest.fixture(autouse=True)
def _clear_capability_bearer_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for k in list(os.environ.keys()):
        if k.startswith("LUMOGIS_CAPABILITY_BEARER_"):
            monkeypatch.delenv(k, raising=False)


async def test_registry_discovers_manifest_and_health_via_mock_transport() -> None:
    m = _mock_manifest()
    transport = httpx.MockTransport(_mock_transport_handler(m))
    reg = CapabilityRegistry(transport=transport)
    await reg.discover([MOCK_BASE])
    svc = reg.get_service(MOCK_CAP_ID)
    assert svc is not None
    assert svc.base_url == MOCK_BASE
    assert svc.manifest.id == MOCK_CAP_ID
    assert svc.manifest.tools[0].name == MOCK_TOOL
    assert svc.healthy is False
    await reg.check_all_health()
    assert svc.healthy is True


def test_build_tool_catalog_mock_echo_healthy_and_unhealthy() -> None:
    reg_h = _FakeRegistry(_registered_service(healthy=True))
    cat_ok = build_tool_catalog(
        tool_specs=list(services_tools.TOOL_SPECS),
        list_actions_fn=lambda: [],
        capability_registry=reg_h,
    )
    rows_ok = [e for e in cat_ok.entries if e.name == MOCK_TOOL]
    assert len(rows_ok) == 1
    e = rows_ok[0]
    assert e.source == "capability"
    assert e.capability_id == MOCK_CAP_ID
    assert e.transport == "catalog_only"
    assert e.available is True
    assert e.why_not_available is None

    reg_bad = _FakeRegistry(_registered_service(healthy=False))
    cat_bad = build_tool_catalog(
        tool_specs=list(services_tools.TOOL_SPECS),
        list_actions_fn=lambda: [],
        capability_registry=reg_bad,
    )
    row = next(x for x in cat_bad.entries if x.name == MOCK_TOOL)
    assert row.available is False
    assert row.why_not_available


def test_me_tools_response_safe_metadata_for_mock_capability() -> None:
    reg = _FakeRegistry(_registered_service(healthy=True))
    resp = build_me_tools_response(
        "user-1",
        catalog_builder=lambda **_k: build_tool_catalog(capability_registry=reg),
    )
    raw = json.dumps(resp.model_dump(mode="json"))
    assert MOCK_TOOL in raw
    assert MOCK_BASE not in raw
    assert "secret-token" not in raw
    row = next(t for t in resp.tools if t.name == MOCK_TOOL)
    assert row.source == "capability"
    assert row.capability_id == MOCK_CAP_ID
    assert row.transport == "catalog_only"


def test_admin_diagnostics_includes_mock_echo_counts_and_no_urls() -> None:
    reg = _FakeRegistry(
        _registered_service(healthy=True),
        RegisteredService(
            manifest=CapabilityManifest(
                name="other",
                id="other-svc",
                version="1",
                type="service",
                transport=CapabilityTransport.HTTP,
                license_mode=CapabilityLicenseMode.COMMUNITY,
                maturity=CapabilityMaturity.PREVIEW,
                description="x",
                tools=[
                    CapabilityTool(
                        name="other.ping",
                        description="other",
                        license_mode=CapabilityLicenseMode.COMMUNITY,
                        input_schema={"type": "object"},
                        output_schema={"type": "object"},
                    )
                ],
                health_endpoint="/health",
                capabilities_endpoint="/capabilities",
                permissions_required=[],
                config_schema={"type": "object"},
                min_core_version="0.1.0",
                maintainer="t",
            ),
            base_url="http://other:1",
            registered_at=datetime.now(timezone.utc),
            healthy=False,
        ),
    )

    def _me(uid: str):
        return build_me_tools_response(
            uid,
            catalog_builder=lambda **_k: build_tool_catalog(capability_registry=reg),
        )

    out = build_admin_diagnostics_response(
        "admin-1",
        capability_registry=reg,
        me_tools_builder=_me,
    )
    cap = out.capabilities
    assert cap.total == 2
    assert cap.healthy == 1
    assert cap.unhealthy == 1
    by_id = {s.id: s for s in cap.services}
    assert MOCK_CAP_ID in by_id
    assert by_id[MOCK_CAP_ID].healthy is True
    assert by_id[MOCK_CAP_ID].tools == 1
    dumped = out.model_dump_json()
    assert MOCK_BASE not in dumped
    assert "http://other" not in dumped


def _patch_httpx_client(monkeypatch: pytest.MonkeyPatch, inner) -> list[httpx.Request]:
    captured: list[httpx.Request] = []

    def _wrap(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return inner(request)

    transport = httpx.MockTransport(_wrap)

    class _P(httpx.Client):
        def __init__(self, *a, **kwargs):
            kwargs.pop("transport", None)
            super().__init__(*a, transport=transport, **kwargs)

    monkeypatch.setattr(httpx, "Client", _P)
    return captured


def test_oop_dispatch_success_headers_and_body(monkeypatch: pytest.MonkeyPatch) -> None:
    def _inner(req: httpx.Request) -> httpx.Response:
        if f"/tools/{MOCK_TOOL}" in str(req.url):
            return httpx.Response(200, text='{"ok":"pong"}')
        return httpx.Response(404)

    cap = _patch_httpx_client(monkeypatch, _inner)
    monkeypatch.setenv("LUMOGIS_TOOL_CATALOG_ENABLED", "true")
    monkeypatch.setenv(BEARER_ENV_KEY, "secret-token")
    reg = _FakeRegistry(_registered_service(healthy=True))

    merged, tok = prepare_llm_tools_for_request("user-z", capability_registry=reg)
    assert tok is not None
    assert any(d.get("name") == MOCK_TOOL for d in merged)
    try:
        monkeypatch.setattr("config.get_capability_registry", lambda: reg)
        monkeypatch.setattr("permissions.check_permission", lambda *a, **k: True)
        out = services_tools.run_tool(MOCK_TOOL, {"msg": "hi"}, user_id="user-z")
    finally:
        finish_llm_tools_request(tok)

    assert "pong" in out
    assert len(cap) == 1
    r0 = cap[0]
    assert r0.method == "POST"
    assert str(r0.url).endswith(f"/tools/{MOCK_TOOL}")
    assert r0.headers.get("x-lumogis-user") == "user-z"
    assert "Bearer secret-token" in (r0.headers.get("authorization") or "")
    body = json.loads(r0.content)
    assert body.get("msg") == "hi"
    assert body.get("user_id") == "user-z"


def test_oop_dispatch_uses_bearer_for_each_capability_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[tuple[str, str]] = []

    def _inner(req: httpx.Request) -> httpx.Response:
        captured.append((req.url.path, req.headers.get("authorization") or ""))
        return httpx.Response(200, text='{"ok":true}')

    _patch_httpx_client(monkeypatch, _inner)
    monkeypatch.setenv("LUMOGIS_TOOL_CATALOG_ENABLED", "true")
    monkeypatch.setenv("LUMOGIS_CAPABILITY_BEARER_ALPHA_SVC", "alpha-token")
    monkeypatch.setenv("LUMOGIS_CAPABILITY_BEARER_ZULU_SVC", "zulu-token")

    def _svc(service_id: str, base: str, tool_name: str) -> RegisteredService:
        manifest = _mock_manifest().model_copy(
            update={
                "id": service_id,
                "name": service_id,
                "tools": [
                    _echo_tool().model_copy(
                        update={"name": tool_name, "description": f"{service_id} tool"}
                    )
                ],
            }
        )
        return RegisteredService(
            manifest=manifest,
            base_url=base,
            registered_at=datetime.now(timezone.utc),
            healthy=True,
        )

    reg = _FakeRegistry(
        _svc("alpha-svc", "http://alpha.test:9", "alpha.echo"),
        _svc("zulu-svc", "http://zulu.test:9", "zulu.echo"),
    )
    merged, tok = prepare_llm_tools_for_request("user-multi", capability_registry=reg)
    assert tok is not None
    assert any(d.get("name") == "alpha.echo" for d in merged)
    assert any(d.get("name") == "zulu.echo" for d in merged)
    try:
        monkeypatch.setattr("config.get_capability_registry", lambda: reg)
        monkeypatch.setattr("permissions.check_permission", lambda *a, **k: True)
        assert "ok" in services_tools.run_tool(
            "alpha.echo", {"msg": "a"}, user_id="user-multi"
        )
        assert "ok" in services_tools.run_tool(
            "zulu.echo", {"msg": "z"}, user_id="user-multi"
        )
    finally:
        finish_llm_tools_request(tok)

    assert captured == [
        ("/tools/alpha.echo", "Bearer alpha-token"),
        ("/tools/zulu.echo", "Bearer zulu-token"),
    ]


def test_flag_off_no_llm_tool_no_dispatch_no_http(monkeypatch: pytest.MonkeyPatch) -> None:
    import services.capability_http as ch

    called: list[str] = []

    def _no_http(*_a, **_k):
        called.append("post_capability")
        raise AssertionError("capability HTTP must not run when catalog flag is off")

    monkeypatch.setattr(ch, "post_capability_tool_invocation", _no_http)
    monkeypatch.setenv("LUMOGIS_TOOL_CATALOG_ENABLED", "false")
    monkeypatch.setenv(BEARER_ENV_KEY, "ignored-when-flag-off")
    reg = _FakeRegistry(_registered_service(healthy=True))

    merged, tok = prepare_llm_tools_for_request("u1", capability_registry=reg)
    assert tok is None
    assert merged is services_tools.TOOLS
    assert not any(d.get("name") == MOCK_TOOL for d in merged)

    assert try_run_oop_capability_tool(MOCK_TOOL, {"msg": "x"}, user_id="u1") is None

    err = json.loads(services_tools.run_tool(MOCK_TOOL, {"msg": "x"}, user_id="u1"))
    assert err.get("error", "").startswith("Unknown tool")
    assert called == []


def test_missing_bearer_no_oop_entry_no_http(monkeypatch: pytest.MonkeyPatch) -> None:
    import services.capability_http as ch

    called: list[str] = []

    def _no_http(*_a, **_k):
        called.append("post")
        return None

    monkeypatch.setattr(ch, "post_capability_tool_invocation", _no_http)
    monkeypatch.setenv("LUMOGIS_TOOL_CATALOG_ENABLED", "true")
    reg = _FakeRegistry(_registered_service(healthy=True))

    merged, tok = prepare_llm_tools_for_request("u2", capability_registry=reg)
    assert tok is None
    assert not any(d.get("name") == MOCK_TOOL for d in merged)
    assert try_run_oop_capability_tool(MOCK_TOOL, {}, user_id="u2") is None
    assert called == []


def test_permission_denied_no_http(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _patch_httpx_client(
        monkeypatch, lambda _r: httpx.Response(200, text="should-not-reach")
    )
    monkeypatch.setenv("LUMOGIS_TOOL_CATALOG_ENABLED", "true")
    monkeypatch.setenv(BEARER_ENV_KEY, "tok")
    reg = _FakeRegistry(_registered_service(healthy=True))
    merged, tok = prepare_llm_tools_for_request("u3", capability_registry=reg)
    assert tok is not None
    try:
        monkeypatch.setattr("config.get_capability_registry", lambda: reg)
        monkeypatch.setattr("permissions.check_permission", lambda *a, **k: False)
        out = services_tools.run_tool(MOCK_TOOL, {}, user_id="u3")
    finally:
        finish_llm_tools_request(tok)

    d = json.loads(out)
    assert d.get("error") == "Permission denied"
    assert cap == []


def test_post_capability_tool_invocation_via_mock_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = _mock_manifest()
    tport = httpx.MockTransport(_mock_transport_handler(manifest))

    class _P(httpx.Client):
        def __init__(self, *a, **kwargs):
            kwargs.pop("transport", None)
            super().__init__(*a, transport=tport, **kwargs)

    monkeypatch.setattr(httpx, "Client", _P)
    res = post_capability_tool_invocation(
        base_url=MOCK_BASE,
        tool_name=MOCK_TOOL,
        user_id="alice",
        json_body={"msg": "ping"},
        timeout_s=5.0,
        service_bearer="tok",
        require_service_bearer=True,
        unavailable_message="down",
    )
    assert res.ok
    assert res.http_status == 200
    assert "pong" in res.text


def test_attribution_without_bearer_rejected_by_mock_endpoint() -> None:
    """Capability-style handler: X-Lumogis-User does not replace Authorization."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == f"/tools/{MOCK_TOOL}":
            auth = request.headers.get("authorization") or ""
            if not auth.startswith("Bearer "):
                saw_u = bool(request.headers.get("x-lumogis-user"))
                return httpx.Response(
                    401,
                    json={"detail": "auth required", "saw_user": saw_u},
                )
            return httpx.Response(200, text="ok")
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        r = client.post(
            f"{MOCK_BASE}/tools/{MOCK_TOOL}",
            headers={"X-Lumogis-User": "eve"},
            json={"msg": "nope"},
        )
    assert r.status_code == 401
    body = r.json()
    assert body.get("detail") == "auth required"
    assert body.get("saw_user") is True


def test_execute_capability_http_audit_on_deny_and_allow(monkeypatch: pytest.MonkeyPatch) -> None:
    audit: list[ToolAuditEnvelope] = []

    def emit(e: ToolAuditEnvelope) -> None:
        audit.append(e)

    ex = ToolExecutor(
        permission=PermissionCheck(check=lambda *a, **k: False),
        emit_audit=emit,
    )
    res = ex.execute_capability_http(
        user_id="u",
        request_id="r1",
        tool_name=MOCK_TOOL,
        capability_id=MOCK_CAP_ID,
        connector=f"capability.{MOCK_CAP_ID}",
        action_type=MOCK_TOOL,
        is_write=False,
        base_url=MOCK_BASE,
        input_={"msg": "a"},
        get_service_bearer=lambda: "t",
        require_service_bearer=True,
        service_healthy=True,
    )
    assert res.denied
    assert audit and audit[0].status == "denied"

    cap = _patch_httpx_client(monkeypatch, lambda _r: httpx.Response(200, text="ok-body"))
    monkeypatch.setenv(BEARER_ENV_KEY, "t")
    audit.clear()
    ex2 = ToolExecutor(
        permission=PermissionCheck(check=lambda *a, **k: True),
        emit_audit=emit,
    )
    res2 = ex2.execute_capability_http(
        user_id="u",
        request_id="r2",
        tool_name=MOCK_TOOL,
        capability_id=MOCK_CAP_ID,
        connector=f"capability.{MOCK_CAP_ID}",
        action_type=MOCK_TOOL,
        is_write=False,
        base_url=MOCK_BASE,
        input_={"msg": "b"},
        get_service_bearer=lambda: "t",
        require_service_bearer=True,
        service_healthy=True,
    )
    assert res2.success
    assert res2.output == "ok-body"
    assert cap
    assert audit[-1].status == "ok"
