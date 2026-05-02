# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""ToolAuditEnvelope emission from ToolExecutor.

OOP durable ``audit_log`` fan-in is composed in
:func:`services.unified_tools.try_run_oop_capability_tool` (not in
:class:`ToolExecutor` itself).
"""

from __future__ import annotations

import httpx
from services.execution import PermissionCheck
from services.execution import ToolAuditEnvelope
from services.execution import ToolExecutor


def _patch_ok(monkeypatch) -> None:
    t = httpx.MockTransport(lambda _r: httpx.Response(200, text='{"r":1}'))

    class _P(httpx.Client):
        def __init__(self, *a, **kw):
            kw.pop("transport", None)
            super().__init__(*a, transport=t, **kw)

    monkeypatch.setattr(httpx, "Client", _P)


def test_success_emits_envelope_with_ids(monkeypatch) -> None:
    out: list[ToolAuditEnvelope] = []

    def sink(e: ToolAuditEnvelope) -> None:
        out.append(e)

    _patch_ok(monkeypatch)
    ex = ToolExecutor(
        permission=PermissionCheck(check=lambda *_: True),
        emit_audit=sink,
    )
    r = ex.execute_capability_http(
        user_id="bob",
        request_id="cor-1",
        tool_name="t1",
        capability_id="cap.svc",
        connector="c",
        action_type="a",
        is_write=False,
        base_url="http://h:1",
        input_={"k": 2},
        get_service_bearer=lambda: "tok",
    )
    assert r.success
    assert out[-1].status == "ok"
    assert out[-1].user_id == "bob"
    assert out[-1].tool_name == "t1"
    assert out[-1].request_id == "cor-1"
    assert out[-1].capability_id == "cap.svc"
    assert out[-1].result_summary is not None


def test_failure_emits_error_status() -> None:
    out: list[ToolAuditEnvelope] = []
    ex = ToolExecutor(
        permission=PermissionCheck(check=lambda *_: True),
        emit_audit=out.append,
    )
    r = ex.execute_capability_http(
        user_id="u",
        request_id="r2",
        tool_name="t",
        capability_id="c",
        connector="c",
        action_type="a",
        is_write=False,
        base_url="http://b",
        input_={},
        get_service_bearer=None,
    )
    assert not r.success
    assert r.blocked_auth
    assert out[-1].status == "forbidden_auth"
    assert out[-1].failure_reason
