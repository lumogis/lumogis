# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Capability invoke contract v1 conformance suite (LUM-41).

Proves the contract against TWO implementations — a mock echo capability and an
inline KG-shaped handler (the AGPL/commercial boundary prevents importing the
real ``services/lumogis-graph`` code, so the KG conformance is mirrored here and
also asserted by that service's own tests). Covers: declared-path decoupling,
request/response envelope round-trip, output-schema validation (pass + fail),
structured errors surfaced on any status, version negotiation, and the byte cap.
"""

from __future__ import annotations

import json

import httpx
import pytest
from models.capability import CapabilityLicenseMode
from models.capability import CapabilityManifest
from models.capability import CapabilityMaturity
from models.capability import CapabilityTool
from models.capability import CapabilityTransport
from models.capability import ToolInvoke
from models.capability_invoke import CapabilityInvokeResponse
from services.capability_registry import CapabilityRegistry

from services import capability_http as ch
from services import capability_output_validator as cov


@pytest.fixture(autouse=True)
def _reset_validator_cache():
    cov.reset_cache_for_tests()
    yield
    cov.reset_cache_for_tests()


def _patch_httpx(monkeypatch, handler) -> list[httpx.Request]:
    captured: list[httpx.Request] = []

    def _wrapped(req: httpx.Request) -> httpx.Response:
        captured.append(req)
        return handler(req)

    transport = httpx.MockTransport(_wrapped)
    real = httpx.Client

    class _Patched(real):
        def __init__(self, *a, **k):
            k.pop("transport", None)
            super().__init__(*a, transport=transport, **k)

    monkeypatch.setattr(httpx, "Client", _Patched)
    return captured


def _invoke(monkeypatch, handler, **kwargs) -> ch.HttpInvokeResult:
    _patch_httpx(monkeypatch, handler)
    base = dict(
        base_url="http://svc.test:1",
        tool_name="t",
        user_id="u",
        arguments={},
        timeout_s=1.0,
        service_bearer="sec",
    )
    base.update(kwargs)
    return ch.post_capability_tool_invocation(**base)


# ---------------------------------------------------------------------------
# Envelope models
# ---------------------------------------------------------------------------


def test_response_requires_output_xor_error():
    CapabilityInvokeResponse.model_validate({"ok": True, "output": None})  # ok
    CapabilityInvokeResponse.model_validate(
        {"ok": False, "error": {"code": "timeout", "message": "x"}}
    )
    with pytest.raises(ValueError):
        CapabilityInvokeResponse.model_validate(
            {"ok": True, "error": {"code": "internal", "message": "x"}}
        )
    with pytest.raises(ValueError):
        CapabilityInvokeResponse.model_validate({"ok": False})


def test_invoke_path_defaults_and_override():
    t = CapabilityTool(
        name="graph.query_ego",
        description="d",
        license_mode=CapabilityLicenseMode.COMMERCIAL,
        input_schema={},
        output_schema={},
    )
    assert t.invoke_path == "/tools/graph.query_ego"
    t2 = t.model_copy(update={"invoke": ToolInvoke(path="/tools/query_graph")})
    assert t2.invoke_path == "/tools/query_graph"


# ---------------------------------------------------------------------------
# Output-schema validation (mandatory when non-trivial)
# ---------------------------------------------------------------------------


def test_output_validator_trivial_schema_skips():
    for schema in ({}, {"type": "string"}, {"type": "object"}):
        cov.validate_output("anything", schema)  # no raise
        cov.validate_output({"x": 1}, schema)


def test_output_validator_non_trivial_passes_and_fails():
    schema = {"type": "object", "properties": {"n": {"type": "integer"}}, "required": ["n"]}
    cov.validate_output({"n": 1}, schema)  # ok
    with pytest.raises(cov.OutputSchemaError):
        cov.validate_output({}, schema)  # missing required
    with pytest.raises(cov.OutputSchemaError):
        cov.validate_output({"n": "not-int"}, schema)


def test_output_validator_cache_recompiles_on_schema_change():
    """Content-addressed cache: a changed schema must not reuse a stale validator."""
    s1 = {"type": "object", "properties": {"a": {"type": "string"}}, "required": ["a"]}
    s2 = {"type": "object", "properties": {"b": {"type": "string"}}, "required": ["b"]}
    cov.validate_output({"a": "x"}, s1)
    # Same conceptual tool, different schema → must validate against s2 now.
    with pytest.raises(cov.OutputSchemaError):
        cov.validate_output({"a": "x"}, s2)
    cov.validate_output({"b": "y"}, s2)


# ---------------------------------------------------------------------------
# Dispatch: declared path, envelope round-trip, structured errors
# ---------------------------------------------------------------------------


def test_dispatch_posts_envelope_to_declared_path(monkeypatch):
    cap = _patch_httpx(
        monkeypatch, lambda _r: httpx.Response(200, json={"ok": True, "output": "ok"})
    )
    r = ch.post_capability_tool_invocation(
        base_url="http://svc.test:1",
        tool_name="graph.query",
        user_id="alice",
        arguments={"mode": "ego"},
        timeout_s=1.0,
        service_bearer="sec",
        invoke_path="/tools/query_graph",
    )
    assert r.ok
    assert cap[0].url == httpx.URL("http://svc.test:1/tools/query_graph")
    body = json.loads(cap[0].content)
    assert body["tool"] == "graph.query"
    assert body["arguments"] == {"mode": "ego"}
    assert body["meta"]["user"] == "alice"


def test_dispatch_parses_output_not_raw_text(monkeypatch):
    r = _invoke(monkeypatch, lambda _r: httpx.Response(200, json={"ok": True, "output": {"k": 1}}))
    assert r.ok
    assert r.output == {"k": 1}


def test_dispatch_surfaces_structured_error_on_200(monkeypatch):
    r = _invoke(
        monkeypatch,
        lambda _r: httpx.Response(
            200,
            json={"ok": False, "error": {"code": "timeout", "message": "slow", "retryable": True}},
        ),
    )
    assert not r.ok
    assert r.error_code == "timeout"
    assert r.retryable is True


def test_dispatch_parses_error_envelope_on_non_200(monkeypatch):
    """The headline ADR-169 fix: KG's structured 504 error must survive."""
    r = _invoke(
        monkeypatch,
        lambda _r: httpx.Response(
            504,
            json={
                "ok": False,
                "error": {"code": "timeout", "message": "budget", "retryable": True},
            },
        ),
    )
    assert not r.ok
    assert r.error_code == "timeout"
    assert r.retryable is True


def test_dispatch_non_envelope_503_maps_to_unavailable(monkeypatch):
    r = _invoke(monkeypatch, lambda _r: httpx.Response(503, text="down"))
    assert not r.ok
    assert r.error_code == "unavailable"
    assert r.retryable is True


def test_dispatch_200_non_envelope_is_internal(monkeypatch):
    """Hard cut: a legacy raw-text 200 is a non-conforming service, not a success."""
    r = _invoke(monkeypatch, lambda _r: httpx.Response(200, text="pong"))
    assert not r.ok
    assert r.error_code == "internal"


def test_dispatch_invalid_output_error(monkeypatch):
    schema = {"type": "object", "properties": {"n": {"type": "integer"}}, "required": ["n"]}
    r = _invoke(
        monkeypatch,
        lambda _r: httpx.Response(200, json={"ok": True, "output": {"n": "not-int"}}),
        output_schema=schema,
    )
    assert not r.ok
    assert r.error_code == "invalid_output"


def test_dispatch_oversized_output_rejected(monkeypatch):
    monkeypatch.setattr(ch, "INVOKE_OUTPUT_MAX_BYTES", 64)
    big = "x" * 500
    r = _invoke(
        monkeypatch,
        lambda _r: httpx.Response(200, json={"ok": True, "output": big}),
    )
    assert not r.ok
    assert r.error_code == "invalid_output"


# ---------------------------------------------------------------------------
# Registry: contract_version negotiation + capabilities_endpoint validation
# ---------------------------------------------------------------------------


def _manifest(**overrides) -> CapabilityManifest:
    base = dict(
        name="Conformance Svc",
        id="conf-svc",
        version="1.0.0",
        type="service",
        transport=CapabilityTransport.HTTP,
        license_mode=CapabilityLicenseMode.COMMUNITY,
        maturity=CapabilityMaturity.PREVIEW,
        description="d",
        tools=[
            CapabilityTool(
                name="conf.echo",
                description="d",
                license_mode=CapabilityLicenseMode.COMMUNITY,
                input_schema={},
                output_schema={},
            )
        ],
        health_endpoint="/health",
        capabilities_endpoint="/capabilities",
        permissions_required=[],
        config_schema={},
        min_core_version="0.1.0",
        maintainer="tests",
    )
    base.update(overrides)
    return CapabilityManifest(**base)


def _discover_with_manifest(monkeypatch, manifest: CapabilityManifest) -> CapabilityRegistry:
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/capabilities":
            return httpx.Response(200, content=manifest.model_dump_json())
        return httpx.Response(404)

    reg = CapabilityRegistry(transport=httpx.MockTransport(handler))
    reg.discover_sync(["http://conf.test:1"])
    return reg


def test_registry_accepts_v1_and_unknown_minor(monkeypatch):
    reg = _discover_with_manifest(monkeypatch, _manifest(contract_version="1.7"))
    assert reg.get_service("conf-svc") is not None


def test_registry_rejects_unknown_major(monkeypatch):
    reg = _discover_with_manifest(monkeypatch, _manifest(contract_version="2.0"))
    assert reg.get_service("conf-svc") is None


def test_registry_rejects_mismatched_capabilities_endpoint(monkeypatch):
    reg = _discover_with_manifest(monkeypatch, _manifest(capabilities_endpoint="/caps"))
    assert reg.get_service("conf-svc") is None
