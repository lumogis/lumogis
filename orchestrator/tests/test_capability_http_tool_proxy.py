# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""HTTP invoke helper for the capability invoke contract v1 (LUM-41).

Exercises ``post_capability_tool_invocation``: it POSTs the v1 request envelope
and returns a structured :class:`HttpInvokeResult` (``ok`` / ``output`` /
``error_code`` / ``error_message`` / ``retryable``).
"""

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


def test_post_succeeds_sends_envelope_and_returns_output(monkeypatch) -> None:
    cap = _patch_httpx_client(
        monkeypatch,
        lambda _r: httpx.Response(200, json={"ok": True, "output": {"pong": True}}),
    )
    r = ch.post_capability_tool_invocation(
        base_url="http://cap.test:1",
        tool_name="my_tool",
        user_id="u1",
        arguments={"a": 1},
        timeout_s=1.0,
        service_bearer="sec",
        require_service_bearer=ch.REQUIRE_BEARER_DEFAULT,
        unavailable_message="nope",
    )
    assert r.ok
    assert r.output == {"pong": True}
    assert len(cap) == 1
    assert cap[0].url == httpx.URL("http://cap.test:1/tools/my_tool")
    assert cap[0].headers["x-lumogis-user"] == "u1"
    assert cap[0].headers["authorization"] == "Bearer sec"
    body = json.loads(cap[0].read().decode())
    assert body["contract_version"] == "1.0"
    assert body["tool"] == "my_tool"
    assert body["arguments"] == {"a": 1}
    assert body["meta"]["user"] == "u1"


def test_post_uses_declared_invoke_path_not_tool_name(monkeypatch) -> None:
    """The v1 decoupling: Core POSTs to the declared invoke path, not /tools/{name}."""
    cap = _patch_httpx_client(
        monkeypatch,
        lambda _r: httpx.Response(200, json={"ok": True, "output": "x"}),
    )
    r = ch.post_capability_tool_invocation(
        base_url="http://cap.test:1",
        tool_name="graph.query",
        user_id="u1",
        arguments={},
        timeout_s=1.0,
        service_bearer="sec",
        invoke_path="/tools/query_graph",
    )
    assert r.ok
    assert cap[0].url == httpx.URL("http://cap.test:1/tools/query_graph")


def test_post_fail_closed_missing_bearer_when_required() -> None:
    r = ch.post_capability_tool_invocation(
        base_url="http://x",
        tool_name="t",
        user_id="u",
        arguments={},
        timeout_s=0.1,
        service_bearer=None,
        require_service_bearer=True,
        unavailable_message="failmsg",
    )
    assert not r.ok
    assert r.error_message == "failmsg"
    assert r.error_code == "missing_service_auth"


@pytest.mark.parametrize(
    "status,code,retryable",
    [(503, "unavailable", True), (504, "timeout", True), (500, "internal", False)],
)
def test_post_non_200_non_envelope_maps_by_status(
    status: int, code: str, retryable: bool, monkeypatch
) -> None:
    _patch_httpx_client(monkeypatch, lambda _r: httpx.Response(status, text="down"))
    r = ch.post_capability_tool_invocation(
        base_url="http://h",
        tool_name="t",
        user_id="u",
        arguments={},
        timeout_s=0.1,
        service_bearer="t",
        require_service_bearer=True,
    )
    assert not r.ok
    assert r.error_code == code
    assert r.retryable is retryable
    assert r.http_status == status


def test_post_parses_error_envelope_on_non_200(monkeypatch) -> None:
    """A structured {ok:false,error} body is surfaced verbatim even on a 504."""
    _patch_httpx_client(
        monkeypatch,
        lambda _r: httpx.Response(
            504,
            json={"ok": False, "error": {"code": "timeout", "message": "slow", "retryable": True}},
        ),
    )
    r = ch.post_capability_tool_invocation(
        base_url="http://h",
        tool_name="t",
        user_id="u",
        arguments={},
        timeout_s=0.1,
        service_bearer="t",
    )
    assert not r.ok
    assert r.error_code == "timeout"
    assert r.error_message == "slow"
    assert r.retryable is True


def test_post_200_non_envelope_is_internal(monkeypatch) -> None:
    """Hard cut: a 200 that is not a v1 envelope is a non-conforming service."""
    _patch_httpx_client(monkeypatch, lambda _r: httpx.Response(200, text="pong"))
    r = ch.post_capability_tool_invocation(
        base_url="http://h",
        tool_name="t",
        user_id="u",
        arguments={},
        timeout_s=0.1,
        service_bearer="t",
    )
    assert not r.ok
    assert r.error_code == "internal"
