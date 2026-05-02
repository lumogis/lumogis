# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Phase 5: OOP ``ToolAuditEnvelope`` fan-in to durable ``audit_log`` rows."""

from __future__ import annotations

import json
import os
from datetime import datetime
from datetime import timezone

import httpx
import pytest
from models.actions import AuditEntry
from models.capability import CapabilityLicenseMode
from models.capability import CapabilityManifest
from models.capability import CapabilityMaturity
from models.capability import CapabilityTool
from models.capability import CapabilityTransport
from services.capability_registry import RegisteredService
from services.execution import CAPABILITY_TOOL_AUDIT_ACTION
from services.execution import ToolAuditEnvelope
from services.execution import persist_tool_audit_envelope
from services.execution import tool_audit_envelope_to_audit_entry
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


def _httpx_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = httpx.MockTransport(lambda _r: httpx.Response(200, text='{"ok":true}'))

    class _P(httpx.Client):
        def __init__(self, *a, **kwargs):
            kwargs.pop("transport", None)
            super().__init__(*a, transport=transport, **kwargs)

    monkeypatch.setattr(httpx, "Client", _P)


@pytest.fixture(autouse=True)
def _clear_bearer_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for k in list(os.environ.keys()):
        if k.startswith("LUMOGIS_CAPABILITY_BEARER_"):
            monkeypatch.delenv(k, raising=False)


def test_envelope_to_audit_entry_maps_ids_and_action() -> None:
    env = ToolAuditEnvelope(
        user_id="alice",
        tool_name="mock.echo_ping",
        request_id="req-99",
        capability_id="mock.echo",
        status="ok",
        failure_reason=None,
        result_summary='{"pong":1}',
        connector="capability.mock.echo",
        action_type="mock.echo_ping",
        is_write=False,
    )
    row = tool_audit_envelope_to_audit_entry(env)
    assert row.action_name == CAPABILITY_TOOL_AUDIT_ACTION
    assert row.user_id == "alice"
    assert row.mode == "ASK"
    assert row.connector == "capability.mock.echo"
    assert "mock.echo" in row.input_summary
    assert "mock.echo_ping" in row.input_summary
    assert "req-99" in row.input_summary
    body = json.loads(row.input_summary)
    assert body["kind"] == "capability_tool"
    assert body["status"] == "ok"
    out = json.loads(row.result_summary)
    assert out["audit_status"] == "ok"
    assert "result_preview" in out


def test_envelope_summaries_exclude_bearer_like_strings() -> None:
    env = ToolAuditEnvelope(
        user_id="u",
        tool_name="t",
        request_id=None,
        capability_id="c1",
        status="forbidden_auth",
        failure_reason="missing service credential",
        result_summary=None,
        connector="capability.c1",
        action_type="t",
        is_write=False,
    )
    row = tool_audit_envelope_to_audit_entry(env)
    assert "Bearer" not in row.input_summary
    assert "Authorization" not in row.input_summary
    assert "lmcp_" not in row.input_summary


def test_persist_uses_injected_writer() -> None:
    seen: list[AuditEntry] = []

    def _fake(entry: AuditEntry, *, reverse_token: str | None = None) -> int | None:
        seen.append(entry)
        assert reverse_token is None
        return 42

    env = ToolAuditEnvelope(
        user_id="u",
        tool_name="tn",
        request_id="r",
        capability_id="cid",
        status="denied",
        failure_reason="permission",
        connector="capability.cid",
        action_type="tn",
        is_write=False,
    )
    rid = persist_tool_audit_envelope(env, write_audit_fn=_fake)
    assert rid == 42
    assert len(seen) == 1
    assert seen[0].action_name == CAPABILITY_TOOL_AUDIT_ACTION


def test_try_run_oop_writes_audit_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    written: list[AuditEntry] = []

    def _capture(entry: AuditEntry, *, reverse_token: str | None = None) -> int:
        written.append(entry)
        return len(written)

    monkeypatch.setattr("actions.audit.write_audit", _capture)
    _httpx_ok(monkeypatch)
    monkeypatch.setenv("LUMOGIS_TOOL_CATALOG_ENABLED", "true")
    monkeypatch.setenv("LUMOGIS_CAPABILITY_BEARER_SVC_AUDIT", "tok-audit")
    tname = "audit.success.tool"
    reg = _Reg(_rsvc("svc.audit", tname, healthy=True))
    _tools_list, oop_tok = prepare_llm_tools_for_request("user-audit", capability_registry=reg)
    try:
        monkeypatch.setattr("config.get_capability_registry", lambda: reg)
        monkeypatch.setattr("permissions.check_permission", lambda *a, **k: True)
        out = try_run_oop_capability_tool(tname, {"q": 1}, user_id="user-audit")
    finally:
        if oop_tok is not None:
            finish_llm_tools_request(oop_tok)

    assert "ok" in out
    assert len(written) == 1
    assert written[0].user_id == "user-audit"
    assert written[0].action_name == CAPABILITY_TOOL_AUDIT_ACTION
    assert written[0].mode == "ASK"
    inp = json.loads(written[0].input_summary)
    assert inp["tool_name"] == tname
    assert inp["capability_id"] == "svc.audit"
    assert inp["status"] == "ok"


def test_try_run_oop_writes_audit_on_permission_denial_no_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    written: list[AuditEntry] = []
    http_hits: list[httpx.Request] = []

    def _capture(entry: AuditEntry, *, reverse_token: str | None = None) -> int:
        written.append(entry)
        return 1

    def _never(_r: httpx.Request) -> httpx.Response:
        http_hits.append(_r)
        return httpx.Response(200, text="should-not-run")

    transport = httpx.MockTransport(_never)

    class _P(httpx.Client):
        def __init__(self, *a, **kwargs):
            kwargs.pop("transport", None)
            super().__init__(*a, transport=transport, **kwargs)

    monkeypatch.setattr(httpx, "Client", _P)
    monkeypatch.setattr("actions.audit.write_audit", _capture)
    monkeypatch.setenv("LUMOGIS_TOOL_CATALOG_ENABLED", "true")
    monkeypatch.setenv("LUMOGIS_CAPABILITY_BEARER_SVC_DENY", "tok")
    tname = "deny.audit.tool"
    reg = _Reg(_rsvc("svc.deny", tname, healthy=True))
    oop_tok = prepare_llm_tools_for_request("u-deny", capability_registry=reg)[1]
    try:
        monkeypatch.setattr("config.get_capability_registry", lambda: reg)
        monkeypatch.setattr("permissions.check_permission", lambda *a, **k: False)
        out = try_run_oop_capability_tool(tname, {}, user_id="u-deny")
    finally:
        if oop_tok is not None:
            finish_llm_tools_request(oop_tok)

    assert "Permission denied" in out
    assert http_hits == []
    assert len(written) == 1
    inp = json.loads(written[0].input_summary)
    assert inp["status"] == "denied"


def test_try_run_oop_writes_audit_on_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    written: list[AuditEntry] = []

    def _capture(entry: AuditEntry, *, reverse_token: str | None = None) -> int:
        written.append(entry)
        return 1

    transport = httpx.MockTransport(lambda _r: httpx.Response(503, text="upstream"))

    class _P(httpx.Client):
        def __init__(self, *a, **kwargs):
            kwargs.pop("transport", None)
            super().__init__(*a, transport=transport, **kwargs)

    monkeypatch.setattr(httpx, "Client", _P)
    monkeypatch.setattr("actions.audit.write_audit", _capture)
    monkeypatch.setenv("LUMOGIS_TOOL_CATALOG_ENABLED", "true")
    monkeypatch.setenv("LUMOGIS_CAPABILITY_BEARER_SVC_ERR", "tok")
    tname = "err.audit.tool"
    reg = _Reg(_rsvc("svc.err", tname, healthy=True))
    oop_tok = prepare_llm_tools_for_request("u-err", capability_registry=reg)[1]
    try:
        monkeypatch.setattr("config.get_capability_registry", lambda: reg)
        monkeypatch.setattr("permissions.check_permission", lambda *a, **k: True)
        out = try_run_oop_capability_tool(tname, {}, user_id="u-err")
    finally:
        if oop_tok is not None:
            finish_llm_tools_request(oop_tok)

    assert out == "capability: service unavailable"
    assert len(written) == 1
    inp = json.loads(written[0].input_summary)
    assert inp["status"] == "error"
    assert inp["failure_reason"] == "http_503"
