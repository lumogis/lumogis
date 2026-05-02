# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""ToolExecutor permission gating for capability HTTP (injected checker in unit tests)."""

from __future__ import annotations

import httpx
from models.tool_spec import ToolSpec
from services.execution import PermissionCheck
from services.execution import ToolExecutor


def _patch_httpx(monkeypatch, handler) -> None:
    transport = httpx.MockTransport(handler)

    class _P(httpx.Client):
        def __init__(self, *a, **kw):
            kw.pop("transport", None)
            super().__init__(*a, transport=transport, **kw)

    monkeypatch.setattr(httpx, "Client", _P)


def test_capability_call_skipped_when_permission_denies(monkeypatch) -> None:
    hit: list[str] = []

    def on_req(_r: httpx.Request) -> httpx.Response:
        hit.append("http")
        return httpx.Response(200, text="ok")

    _patch_httpx(monkeypatch, on_req)
    ex = ToolExecutor(
        permission=PermissionCheck(check=lambda *_: False),
        emit_audit=lambda _e: None,
    )
    r = ex.execute_capability_http(
        user_id="alice",
        request_id="req-1",
        tool_name="t1",
        capability_id="cap.z",
        connector="c1",
        action_type="a1",
        is_write=False,
        base_url="http://svc:9",
        input_={},
        get_service_bearer=lambda: "sec",
    )
    assert r.denied
    assert r.output == "Permission denied"
    assert hit == []


def test_empty_connector_fails_closed() -> None:
    ex = ToolExecutor(
        permission=PermissionCheck(check=lambda *a: True),
        emit_audit=lambda _e: None,
    )
    r = ex.execute_capability_http(
        user_id="u",
        request_id=None,
        tool_name="t",
        capability_id="c",
        connector="   ",
        action_type="a",
        is_write=False,
        base_url="http://x",
        input_={},
        get_service_bearer=lambda: "s",
    )
    assert not r.success
    assert not r.denied
    assert "unavailable" in r.output


def test_inprocess_denied_when_permission_false() -> None:
    def _h(input_: dict, *, user_id: str) -> str:
        return "x"

    spec = ToolSpec(
        name="n",
        connector="fs",
        action_type="read",
        is_write=False,
        definition={"name": "n"},
        handler=_h,
    )
    ex = ToolExecutor(
        permission=PermissionCheck(check=lambda *_: False),
        emit_audit=lambda _e: None,
    )
    r = ex.execute_inprocess(spec, {}, user_id="u", request_id="r1")
    assert r.denied
    assert "Permission denied" in r.output
