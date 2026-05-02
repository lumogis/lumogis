# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""HTTP invoke helper for ``POST {base}/tools/{name}`` (capability services)."""

from __future__ import annotations

import json

import httpx
import pytest

from services import capability_http as ch


def _patch_httpx_client(monkeypatch, handler) -> list[httpx.Request]:
    captured: list[httpx.Request] = []

    def _wrapped(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return handler(request)

    transport = httpx.MockTransport(_wrapped)
    real = httpx.Client

    class _Patched(real):
        def __init__(self, *args, **kwargs):
            kwargs.pop("transport", None)
            super().__init__(*args, transport=transport, **kwargs)

    monkeypatch.setattr(httpx, "Client", _Patched)
    return captured


def test_post_succeeds_includes_user_and_bearer(monkeypatch) -> None:
    cap = _patch_httpx_client(
        monkeypatch,
        lambda _r: httpx.Response(200, json={"ok": True}),
    )
    r = ch.post_capability_tool_invocation(
        base_url="http://cap.test:1",
        tool_name="my_tool",
        user_id="u1",
        json_body={"a": 1},
        timeout_s=1.0,
        service_bearer="sec",
        require_service_bearer=ch.REQUIRE_BEARER_DEFAULT,
        unavailable_message="nope",
    )
    assert r.ok
    assert json.loads(r.text) == {"ok": True}
    assert len(cap) == 1
    assert cap[0].url == httpx.URL("http://cap.test:1/tools/my_tool")
    assert cap[0].headers["x-lumogis-user"] == "u1"
    assert cap[0].headers["authorization"] == "Bearer sec"
    assert json.loads(cap[0].read().decode()) == {"a": 1}


def test_post_fail_closed_missing_bearer_when_required() -> None:
    r = ch.post_capability_tool_invocation(
        base_url="http://x",
        tool_name="t",
        user_id="u",
        json_body={},
        timeout_s=0.1,
        service_bearer=None,
        require_service_bearer=True,
        unavailable_message="failmsg",
    )
    assert not r.ok
    assert r.text == "failmsg"
    assert r.error_reason == "missing_service_auth"


@pytest.mark.parametrize("status", [503, 504, 500])
def test_post_fail_soft_non_200(status: int, monkeypatch) -> None:
    _patch_httpx_client(monkeypatch, lambda _r: httpx.Response(status, text="down"))
    r = ch.post_capability_tool_invocation(
        base_url="http://h",
        tool_name="t",
        user_id="u",
        json_body={},
        timeout_s=0.1,
        service_bearer="t",
        require_service_bearer=True,
    )
    assert not r.ok
    assert "unavailable" in r.text
    assert r.http_status == status
