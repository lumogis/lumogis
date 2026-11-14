# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Unit tests for the `query_graph` proxy ToolSpec (services/tools.py).

When GRAPH_MODE=service, Core's in-process plugin self-disables and the
`query_graph` ToolSpec is registered by `register_query_graph_proxy()`
instead. The handler POSTs to the KG service's
`POST /tools/query_graph` endpoint with the v1 invoke envelope
``{"contract_version","tool","arguments","meta"}`` (LUM-41). These tests pin:

  * the JSON schema is byte-identical to the in-process plugin's spec
    (so prompts and fine-tunes don't have to know which mode is active);
  * the handler returns a stringified error message — never an exception
    — on every failure path the LLM might encounter (offline, 5xx, 504
    timeout, malformed body, max_depth above the cap);
  * `Event.TOOL_REGISTERED` fires exactly once per call so
    `_add_plugin_tool` picks up the spec via the existing hook chain;
  * the bearer token is attached when `GRAPH_WEBHOOK_SECRET` is set;
  * when the secret is absent, Core **fail-closes** (no outbound HTTP)
    unless `LUMOGIS_GRAPH_PROXY_ALLOW_INSECURE_MISSING_SECRET` enables the
    legacy unsigned POST path (see `config.get_graph_proxy_require_service_bearer`).
"""

from __future__ import annotations

import json
import logging

import hooks
import httpx
import pytest
from events import Event
from models.tool_spec import ToolSpec

from services import capability_http
from services import kg_premium_core
from services import tools as services_tools


@pytest.fixture(autouse=True)
def _isolate_tool_state(monkeypatch):
    """Ensure each test starts from a clean tool-registry + KG URL.

    Other tests in the suite call `hooks.shutdown()` in their teardown,
    which clears `_listeners` globally — that wipes
    `services/tools.py:_add_plugin_tool` (registered at module import,
    not per-test). Without `_add_plugin_tool`, firing
    `Event.TOOL_REGISTERED` here would be a silent no-op and TOOL_SPECS
    would never see the proxy's spec. So we explicitly re-attach
    `_add_plugin_tool` if it's missing, then snapshot+restore the
    listener list and prune our specs at teardown.
    """
    monkeypatch.setenv("KG_SERVICE_URL", "http://kg-test.local:8001")
    monkeypatch.delenv("GRAPH_WEBHOOK_SECRET", raising=False)
    monkeypatch.delenv("LUMOGIS_GRAPH_PROXY_ALLOW_INSECURE_MISSING_SECRET", raising=False)
    if services_tools._add_plugin_tool not in hooks._listeners.get(Event.TOOL_REGISTERED, []):
        hooks.register(Event.TOOL_REGISTERED, services_tools._add_plugin_tool)
    snapshot = list(hooks._listeners.get(Event.TOOL_REGISTERED, []))
    yield
    services_tools.TOOL_SPECS[:] = [s for s in services_tools.TOOL_SPECS if s.name != "query_graph"]
    services_tools.TOOLS[:] = [d for d in services_tools.TOOLS if d.get("name") != "query_graph"]
    hooks._listeners[Event.TOOL_REGISTERED] = snapshot


def _patch_proxy_client(monkeypatch, handler):
    """Replace `httpx.Client` inside `_query_graph_proxy_handler` with a
    MockTransport-backed client that captures requests and returns a fixed
    response (or raises). Returns the captured-requests list.
    """
    captured: list[httpx.Request] = []

    def _wrapped(request):
        captured.append(request)
        return handler(request)

    transport = httpx.MockTransport(_wrapped)

    real_client_cls = httpx.Client

    class _PatchedClient(real_client_cls):  # type: ignore[misc, valid-type]
        def __init__(self, *args, **kwargs):
            kwargs.pop("transport", None)
            super().__init__(*args, transport=transport, **kwargs)

    monkeypatch.setattr("httpx.Client", _PatchedClient)
    return captured


# ---------------------------------------------------------------------------
# Spec construction & registration
# ---------------------------------------------------------------------------


def test_register_query_graph_proxy_fires_tool_registered():
    fired: list = []
    hooks.register(Event.TOOL_REGISTERED, lambda spec: fired.append(spec))

    kg_premium_core.register_query_graph_proxy()

    assert len(fired) == 1
    spec = fired[0]
    assert isinstance(spec, ToolSpec)
    assert spec.name == "query_graph"
    assert spec.connector == "lumogis-graph"
    assert spec.is_write is False


def test_register_query_graph_proxy_schema_matches_plugin():
    """The proxy spec MUST mirror plugins/graph/__init__.py's schema or
    LLM tool-call signature drifts between modes (silent prompt regression).
    """
    kg_premium_core.register_query_graph_proxy()
    spec = next(s for s in services_tools.TOOL_SPECS if s.name == "query_graph")
    params = spec.definition["parameters"]

    assert params["required"] == ["mode"]
    assert set(params["properties"]) == {
        "mode",
        "entity",
        "from_entity",
        "to_entity",
        "depth",
        "max_depth",
        "limit",
    }
    assert params["properties"]["mode"]["enum"] == ["ego", "path", "mentions"]
    assert params["properties"]["max_depth"]["maximum"] == 4
    assert params["properties"]["depth"]["maximum"] == 1
    assert params["properties"]["limit"]["maximum"] == 20


# ---------------------------------------------------------------------------
# Handler behaviour
# ---------------------------------------------------------------------------


def test_query_graph_proxy_wraps_payload_in_v1_envelope(monkeypatch):
    """KG ``routes/tools.py`` expects the v1 invoke envelope: contract_version /
    tool / arguments / meta — the args (with user_id) live under ``arguments``."""
    monkeypatch.setenv("LUMOGIS_GRAPH_PROXY_ALLOW_INSECURE_MISSING_SECRET", "true")
    captured = _patch_proxy_client(
        monkeypatch,
        lambda _req: httpx.Response(200, json={"ok": True, "output": "x"}),
    )
    kg_premium_core._query_graph_proxy_handler({"mode": "ego", "entity": "Ada"}, user_id="alice")
    body = json.loads(captured[0].read())
    assert set(body.keys()) == {"contract_version", "tool", "arguments", "meta"}
    assert body["contract_version"] == "1.0"
    assert body["tool"] == "graph.query"
    inner = body["arguments"]
    assert inner["mode"] == "ego"
    assert inner["entity"] == "Ada"
    assert inner["user_id"] == "alice"
    # meta.user is attribution only (mirrors X-Lumogis-User), never scoping.
    assert body["meta"]["user"] == "alice"
    assert body["meta"]["request_id"]


def test_proxy_handler_posts_to_kg_service(monkeypatch):
    monkeypatch.setenv("LUMOGIS_GRAPH_PROXY_ALLOW_INSECURE_MISSING_SECRET", "true")
    sample_response = {"results": [{"a": 1}], "summary": "ok"}
    captured = _patch_proxy_client(
        monkeypatch,
        lambda _req: httpx.Response(200, json={"ok": True, "output": sample_response}),
    )

    out = kg_premium_core._query_graph_proxy_handler(
        {"mode": "ego", "entity": "Ada"}, user_id="alice"
    )

    # The bridge returns the parsed `output` (stringified), not the raw envelope.
    assert json.loads(out) == sample_response
    assert len(captured) == 1
    req = captured[0]
    assert req.method == "POST"
    assert req.url == httpx.URL("http://kg-test.local:8001/tools/query_graph")
    body = json.loads(req.read())
    assert set(body.keys()) == {"contract_version", "tool", "arguments", "meta"}
    inner = body["arguments"]
    assert inner["mode"] == "ego"
    assert inner["entity"] == "Ada"
    assert inner["user_id"] == "alice"
    assert req.headers["x-lumogis-user"] == "alice"


def test_proxy_handler_returns_error_string_on_kg_503(monkeypatch, caplog):
    monkeypatch.setenv("LUMOGIS_GRAPH_PROXY_ALLOW_INSECURE_MISSING_SECRET", "true")
    _patch_proxy_client(
        monkeypatch,
        lambda _req: httpx.Response(503, text="kg down"),
    )

    with caplog.at_level(logging.WARNING, logger="services.capability_http"):
        out = kg_premium_core._query_graph_proxy_handler(
            {"mode": "ego", "entity": "Ada"}, user_id="alice"
        )

    assert out == "query_graph: graph service unavailable"
    assert any("returned 503" in r.message for r in caplog.records)


def test_proxy_handler_returns_error_string_on_kg_504_timeout(monkeypatch, caplog):
    """The KG service returns 504 with `{"reason": "timeout"}` when its own
    2 s budget is exceeded. The proxy must convert that into a clean string
    so the LLM sees a usable response — no exception propagates."""
    monkeypatch.setenv("LUMOGIS_GRAPH_PROXY_ALLOW_INSECURE_MISSING_SECRET", "true")
    _patch_proxy_client(
        monkeypatch,
        lambda _req: httpx.Response(
            504, json={"detail": "graph query exceeded budget", "reason": "timeout"}
        ),
    )

    with caplog.at_level(logging.WARNING, logger="services.capability_http"):
        out = kg_premium_core._query_graph_proxy_handler(
            {"mode": "ego", "entity": "Ada"}, user_id="alice"
        )

    assert out == "query_graph: graph service unavailable"
    assert any("returned 504" in r.message for r in caplog.records)


def test_proxy_handler_returns_error_string_on_network_error(monkeypatch):
    """Requires legacy opt-in with no secret so HTTP is attempted; otherwise
    fail-closed returns the same string before ``httpx`` runs (would not
    exercise transport errors)."""
    monkeypatch.setenv("LUMOGIS_GRAPH_PROXY_ALLOW_INSECURE_MISSING_SECRET", "true")

    def boom(_req):
        raise httpx.ConnectError("no route")

    _patch_proxy_client(monkeypatch, boom)

    out = kg_premium_core._query_graph_proxy_handler(
        {"mode": "ego", "entity": "Ada"}, user_id="alice"
    )
    assert out == "query_graph: graph service unavailable"


def test_proxy_handler_caps_max_depth_at_4(monkeypatch, caplog):
    """Defence-in-depth: even though the JSON schema clamps `max_depth` to 4,
    a misbehaving LLM can post `max_depth: 99`. The handler must reject it
    BEFORE the HTTP call so we don't push pathological depths to KG.
    """
    captured = _patch_proxy_client(
        monkeypatch,
        lambda _req: httpx.Response(200, json={"results": []}),
    )

    with caplog.at_level(logging.WARNING, logger="services.capability_http"):
        out = kg_premium_core._query_graph_proxy_handler(
            {"mode": "path", "from_entity": "A", "to_entity": "B", "max_depth": 99},
            user_id="alice",
        )

    assert out == "query_graph: graph service unavailable"
    assert captured == [], "no HTTP call must be made when max_depth exceeds the cap"
    assert any("rejected max_depth=99" in r.message for r in caplog.records)


def test_proxy_handler_attaches_bearer(monkeypatch):
    """Bearer path when ``GRAPH_WEBHOOK_SECRET`` is set (unchanged intent)."""
    monkeypatch.setenv("GRAPH_WEBHOOK_SECRET", "qg-secret")
    captured = _patch_proxy_client(
        monkeypatch,
        lambda _req: httpx.Response(200, json={"results": []}),
    )

    kg_premium_core._query_graph_proxy_handler({"mode": "ego", "entity": "Ada"}, user_id="alice")

    assert captured[0].headers["authorization"] == "Bearer qg-secret"


def test_proxy_handler_omits_bearer_legacy_opt_in(monkeypatch):
    """Explicit Core opt-in + absent secret → POST without ``Authorization`` (KG may still
    accept via ``KG_ALLOW_INSECURE_WEBHOOKS`` — independent matrix)."""
    monkeypatch.setenv("LUMOGIS_GRAPH_PROXY_ALLOW_INSECURE_MISSING_SECRET", "true")
    captured = _patch_proxy_client(
        monkeypatch,
        lambda _req: httpx.Response(200, json={"results": []}),
    )

    kg_premium_core._query_graph_proxy_handler({"mode": "ego", "entity": "Ada"}, user_id="alice")

    assert len(captured) == 1
    assert "authorization" not in captured[0].headers


def test_proxy_handler_fail_closed_no_opt_in(monkeypatch):
    """No secret and no opt-in → no outbound HTTP (secure default)."""
    captured = _patch_proxy_client(
        monkeypatch,
        lambda _req: httpx.Response(200, json={"results": []}),
    )
    out = kg_premium_core._query_graph_proxy_handler(
        {"mode": "ego", "entity": "Ada"}, user_id="alice"
    )
    assert captured == []
    assert out == kg_premium_core.GRAPH_QUERY_UNAVAILABLE


def test_graph_proxy_fail_closed_when_secret_missing_and_no_opt_in(monkeypatch):
    captured = _patch_proxy_client(
        monkeypatch,
        lambda _req: httpx.Response(200, json={"results": []}),
    )
    results: list = []
    real_post = capability_http.post_capability_tool_invocation

    def spy(*args, **kwargs):
        r = real_post(*args, **kwargs)
        results.append(r)
        return r

    monkeypatch.setattr("services.kg_premium_core.post_capability_tool_invocation", spy)

    out = kg_premium_core._query_graph_proxy_handler(
        {"mode": "ego", "entity": "Ada"}, user_id="alice"
    )

    assert captured == []
    assert out == kg_premium_core.GRAPH_QUERY_UNAVAILABLE
    assert len(results) == 1
    assert results[0].error_code == "missing_service_auth"
    assert results[0].ok is False
