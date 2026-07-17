# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""LUM-613 — capability sandbox + egress: trust predicate, fail-closed gate,
origin-conflict refusal, eviction, and drift audit.

Pure-policy helpers + registry behaviour. No `tethered` (deferred to LUM-619).
"""

from __future__ import annotations

from datetime import datetime
from datetime import timezone

import httpx
import pytest
from models.capability import CapabilityLicenseMode
from models.capability import CapabilityManifest
from models.capability import CapabilityMaturity
from models.capability import CapabilityTool
from models.capability import CapabilityTransport
from models.capability import normalise_external_endpoints
from services.capability_egress import UntrustedInProcessPluginError
from services.capability_egress import assert_first_party_plugin
from services.capability_egress import is_community_capability
from services.capability_egress import is_community_dispatch_allowed
from services.capability_egress import load_first_party_capabilities
from services.capability_registry import CapabilityRegistry
from services.capability_registry import RegisteredService
from services.unified_tools import OOP_TOOL_ROUTES
from services.unified_tools import OopCapabilityToolRoute
from services.unified_tools import _collect_oop_eligible
from services.unified_tools import try_run_oop_capability_tool

# --------------------------------------------------------------------------
# normalise_external_endpoints (validator; re-exported via capability_egress)
# --------------------------------------------------------------------------


def test_normalise_external_endpoints_lowercases_and_dedupes() -> None:
    assert normalise_external_endpoints(["API.Foo.com", "api.foo.com"]) == ["api.foo.com"]


def test_normalise_external_endpoints_accepts_ipv4_rejects_ipv6() -> None:
    assert normalise_external_endpoints(["203.0.113.7"]) == ["203.0.113.7"]
    for bad in ["2001:db8::1", "::1"]:
        with pytest.raises(ValueError):
            normalise_external_endpoints([bad])


@pytest.mark.parametrize(
    "bad",
    [
        "https://x",
        "x/y",
        "*.foo",
        "api.foo.com:8443",
        "exämple.com",
        "",
        # degenerate hosts (fix #4): empty / leading-trailing-dot / consecutive-dot /
        # leading-trailing-hyphen labels must be rejected, not silently accepted.
        ".",
        "-",
        ".foo.com",
        "foo.com.",
        "api..foo",
        "-foo.com",
        "foo.com-",
    ],
)
def test_normalise_external_endpoints_rejects_bad_forms(bad: str) -> None:
    with pytest.raises(ValueError):
        normalise_external_endpoints([bad])


def test_normalise_external_endpoints_accepts_valid_hosts_and_ipv4() -> None:
    assert normalise_external_endpoints(
        ["api.foo.com", "a-b.example.co", "localhost", "203.0.113.7"]
    ) == ["api.foo.com", "a-b.example.co", "localhost", "203.0.113.7"]


def test_normalise_external_endpoints_caps_count_and_length() -> None:
    with pytest.raises(ValueError):
        normalise_external_endpoints(["h"] * 33)
    with pytest.raises(ValueError):
        normalise_external_endpoints(["a" * 300])


# --------------------------------------------------------------------------
# Trust predicate (origin-pinned) + fail-closed gate
# --------------------------------------------------------------------------

_FP = {"lumogis-graph": "http://lumogis-graph:8001"}


def _ic(cap_id: str, base_url: str) -> bool:
    return is_community_capability(capability_id=cap_id, base_url=base_url, first_party=_FP)


def test_is_community_false_for_first_party_id_at_pinned_origin() -> None:
    # Trailing slash / case must not matter; native pinned origin works the same.
    assert _ic("lumogis-graph", "http://lumogis-graph:8001/") is False


def test_is_community_true_for_first_party_id_at_wrong_origin() -> None:
    assert _ic("lumogis-graph", "http://evil:8001") is True


def test_is_community_true_for_unknown_id() -> None:
    assert _ic("community.widget", "http://anything") is True


def test_dispatch_gate_fail_closed_by_default() -> None:
    assert is_community_dispatch_allowed(is_community=False, opt_in=False) is True  # first-party
    assert is_community_dispatch_allowed(is_community=True, opt_in=False) is False  # refused
    assert is_community_dispatch_allowed(is_community=True, opt_in=True) is True  # opt-in


# --------------------------------------------------------------------------
# LUM-618 — containment gate (is_dispatch_allowed) + shim + contained loader
# --------------------------------------------------------------------------


def test_is_dispatch_allowed_containment() -> None:
    from services.capability_egress import is_dispatch_allowed

    contained = frozenset({"acme.contained"})
    # First-party (not community) always dispatches, regardless of containment.
    assert (
        is_dispatch_allowed(
            is_community=False,
            capability_id="anything",
            contained_ids=contained,
            legacy_opt_in=False,
        )
        is True
    )
    # Community + contained → allowed WITHOUT the legacy flag.
    assert (
        is_dispatch_allowed(
            is_community=True,
            capability_id="acme.contained",
            contained_ids=contained,
            legacy_opt_in=False,
        )
        is True
    )
    # Community + NOT contained + no flag → refused (fail-closed).
    assert (
        is_dispatch_allowed(
            is_community=True,
            capability_id="acme.other",
            contained_ids=contained,
            legacy_opt_in=False,
        )
        is False
    )
    # Community + not contained + legacy flag → allowed (deprecated escape hatch).
    assert (
        is_dispatch_allowed(
            is_community=True,
            capability_id="acme.other",
            contained_ids=contained,
            legacy_opt_in=True,
        )
        is True
    )
    # Community + capability_id None (unknown) + not flagged → refused.
    assert (
        is_dispatch_allowed(
            is_community=True, capability_id=None, contained_ids=contained, legacy_opt_in=False
        )
        is False
    )


def test_is_community_dispatch_allowed_shim_preserves_behaviour() -> None:
    # The shim must collapse to `not is_community or opt_in` (no containment term).
    assert is_community_dispatch_allowed(is_community=False, opt_in=False) is True
    assert is_community_dispatch_allowed(is_community=True, opt_in=False) is False
    assert is_community_dispatch_allowed(is_community=True, opt_in=True) is True


def test_load_contained_capabilities_parses_ids(tmp_path, monkeypatch) -> None:
    from services.capability_egress import load_contained_capabilities

    f = tmp_path / "contained.txt"
    f.write_text("# marker\nacme.one\n\nacme.two\n", encoding="utf-8")
    monkeypatch.setenv("LUMOGIS_CONTAINED_CAPABILITIES_FILE", str(f))
    assert load_contained_capabilities(refresh=True) == frozenset({"acme.one", "acme.two"})


def test_load_contained_capabilities_missing_file_is_empty(tmp_path, monkeypatch) -> None:
    from services.capability_egress import load_contained_capabilities

    monkeypatch.setenv("LUMOGIS_CONTAINED_CAPABILITIES_FILE", str(tmp_path / "nope.txt"))
    assert load_contained_capabilities(refresh=True) == frozenset()


def test_load_contained_capabilities_skips_malformed_lines(tmp_path, monkeypatch) -> None:
    from services.capability_egress import load_contained_capabilities

    f = tmp_path / "contained.txt"
    # A line with whitespace is malformed (ids never contain spaces) → skipped.
    f.write_text("acme.ok\nnot a valid id\n", encoding="utf-8")
    monkeypatch.setenv("LUMOGIS_CONTAINED_CAPABILITIES_FILE", str(f))
    assert load_contained_capabilities(refresh=True) == frozenset({"acme.ok"})


def test_load_contained_capabilities_strips_inline_comments(tmp_path, monkeypatch) -> None:
    from services.capability_egress import load_contained_capabilities

    f = tmp_path / "contained.txt"
    # An inline comment after an id must not drop the id (ids never contain '#').
    f.write_text("acme.one  # my capability\n  acme.two\t# another\n", encoding="utf-8")
    monkeypatch.setenv("LUMOGIS_CONTAINED_CAPABILITIES_FILE", str(f))
    assert load_contained_capabilities(refresh=True) == frozenset({"acme.one", "acme.two"})


def test_load_contained_capabilities_mtime_reload(tmp_path, monkeypatch) -> None:
    import os

    from services.capability_egress import load_contained_capabilities

    f = tmp_path / "contained.txt"
    f.write_text("acme.one\n", encoding="utf-8")
    monkeypatch.setenv("LUMOGIS_CONTAINED_CAPABILITIES_FILE", str(f))
    assert load_contained_capabilities(refresh=True) == frozenset({"acme.one"})
    f.write_text("acme.one\nacme.two\n", encoding="utf-8")
    os.utime(f, (f.stat().st_atime, f.stat().st_mtime + 5))
    assert load_contained_capabilities() == frozenset({"acme.one", "acme.two"})


def test_load_contained_capabilities_keep_last_good_on_disappear(
    tmp_path, monkeypatch
) -> None:
    from services.capability_egress import load_contained_capabilities

    f = tmp_path / "contained.txt"
    f.write_text("acme.one\n", encoding="utf-8")
    monkeypatch.setenv("LUMOGIS_CONTAINED_CAPABILITIES_FILE", str(f))
    assert load_contained_capabilities(refresh=True) == frozenset({"acme.one"})
    f.unlink()
    assert load_contained_capabilities() == frozenset({"acme.one"})


# --------------------------------------------------------------------------
# first_party_capabilities.txt loader
# --------------------------------------------------------------------------


def test_load_first_party_capabilities_parses_id_origin_pairs(tmp_path, monkeypatch) -> None:
    f = tmp_path / "fp.txt"
    f.write_text("# comment\nlumogis-graph  http://lumogis-graph:8001\n\n", encoding="utf-8")
    monkeypatch.setenv("LUMOGIS_FIRST_PARTY_CAPABILITIES_FILE", str(f))
    mapping = load_first_party_capabilities(refresh=True)
    assert mapping == {"lumogis-graph": "http://lumogis-graph:8001"}


def test_load_first_party_capabilities_missing_file_is_empty(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("LUMOGIS_FIRST_PARTY_CAPABILITIES_FILE", str(tmp_path / "nope.txt"))
    assert load_first_party_capabilities(refresh=True) == {}


# --------------------------------------------------------------------------
# In-process plugin refusal
# --------------------------------------------------------------------------


def test_assert_first_party_plugin_refuses_unknown() -> None:
    with pytest.raises(UntrustedInProcessPluginError):
        assert_first_party_plugin("evil_plugin", first_party=frozenset({"graph"}))
    assert_first_party_plugin("graph", first_party=frozenset({"graph"}))  # no raise


# --------------------------------------------------------------------------
# Registry: origin-conflict refusal, eviction, drift audit (MockTransport)
# --------------------------------------------------------------------------


def _manifest(
    cap_id: str, *, endpoints: list[str] | None = None, version: str = "1.0.0"
) -> CapabilityManifest:
    return CapabilityManifest(
        name=cap_id,
        id=cap_id,
        version=version,
        type="service",
        transport=CapabilityTransport.HTTP,
        license_mode=CapabilityLicenseMode.COMMUNITY,
        maturity=CapabilityMaturity.PREVIEW,
        description="test",
        tools=[
            CapabilityTool(
                name=f"{cap_id}.ping",
                description="ping",
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
        maintainer="tests",
        external_endpoints=endpoints or [],
    )


def _serve(manifest: CapabilityManifest | bytes):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/capabilities":
            if isinstance(manifest, bytes):
                return httpx.Response(200, content=manifest)
            return httpx.Response(200, content=manifest.model_dump_json())
        return httpx.Response(404)

    return handler


async def test_upsert_refuses_base_url_change_for_existing_id() -> None:
    reg = CapabilityRegistry(transport=httpx.MockTransport(_serve(_manifest("svc-a"))))
    await reg.discover(["http://origin-one:9"])
    assert reg.get_service("svc-a").base_url == "http://origin-one:9"
    # A shadow answers with the same id from a different origin — must be refused.
    reg2_transport = httpx.MockTransport(_serve(_manifest("svc-a")))
    reg._transport = reg2_transport
    await reg.discover(["http://origin-two:9"])
    # unchanged — the origin conflict was refused, keeping the original registration.
    assert reg.get_service("svc-a").base_url == "http://origin-one:9"


async def test_evict_on_content_validation_failure() -> None:
    reg = CapabilityRegistry(transport=httpx.MockTransport(_serve(_manifest("svc-b"))))
    await reg.discover(["http://svc-b:9"])
    assert reg.get_service("svc-b") is not None
    # Same origin now returns garbage → content-validation failure → evicted.
    reg._transport = httpx.MockTransport(_serve(b"not json at all"))
    await reg.discover(["http://svc-b:9"])
    assert reg.get_service("svc-b") is None


async def test_no_evict_on_transient_failure() -> None:
    reg = CapabilityRegistry(transport=httpx.MockTransport(_serve(_manifest("svc-c"))))
    await reg.discover(["http://svc-c:9"])
    assert reg.get_service("svc-c") is not None

    def _boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    reg._transport = httpx.MockTransport(_boom)
    await reg.discover(["http://svc-c:9"])
    assert reg.get_service("svc-c") is not None  # soft dependency — kept


async def test_endpoint_drift_recomputed_on_refresh() -> None:
    reg = CapabilityRegistry(
        transport=httpx.MockTransport(_serve(_manifest("svc-d", endpoints=["a.com"])))
    )
    await reg.discover(["http://svc-d:9"])
    assert reg.get_service("svc-d").external_endpoints == ("a.com",)
    reg._transport = httpx.MockTransport(_serve(_manifest("svc-d", endpoints=["b.com"])))
    await reg.discover(["http://svc-d:9"])
    assert reg.get_service("svc-d").external_endpoints == ("b.com",)  # recomputed, not stale


# --------------------------------------------------------------------------
# Loop-split gate: community hidden from LLM catalog but route survives; refused
# --------------------------------------------------------------------------


def _registered(cap_id: str, *, is_community: bool) -> RegisteredService:
    return RegisteredService(
        manifest=_manifest(cap_id),
        base_url=f"http://{cap_id}:9",
        registered_at=datetime.now(timezone.utc),
        healthy=True,
        is_community=is_community,
    )


class _FakeReg:
    def __init__(self, *svcs: RegisteredService) -> None:
        self._svcs = list(svcs)

    def all_services(self) -> list[RegisteredService]:
        return self._svcs


@pytest.fixture(autouse=True)
def _bearers(monkeypatch: pytest.MonkeyPatch) -> None:
    # _collect_oop_eligible is fail-closed without a per-service bearer.
    monkeypatch.setenv("LUMOGIS_CAPABILITY_BEARER_FIRST_PARTY", "t")
    monkeypatch.setenv("LUMOGIS_CAPABILITY_BEARER_COMMUNITY", "t")


def test_community_hidden_from_llm_catalog_but_route_survives(monkeypatch) -> None:
    monkeypatch.delenv("LUMOGIS_ALLOW_UNCONTAINED_COMMUNITY_CAPABILITIES", raising=False)
    reg = _FakeReg(
        _registered("first-party", is_community=False),
        _registered("community", is_community=True),
    )
    routes, extra_defs = _collect_oop_eligible(reg, set())
    names_in_catalog = {d["name"] for d in extra_defs}
    assert "first-party.ping" in names_in_catalog
    assert "community.ping" not in names_in_catalog  # hidden from LLM
    assert "community.ping" in routes  # but route survives so dispatch is gated, not "unknown"


def test_community_visible_in_catalog_with_optin(monkeypatch) -> None:
    monkeypatch.setenv("LUMOGIS_ALLOW_UNCONTAINED_COMMUNITY_CAPABILITIES", "true")
    reg = _FakeReg(_registered("community", is_community=True))
    _routes, extra_defs = _collect_oop_eligible(reg, set())
    assert "community.ping" in {d["name"] for d in extra_defs}


def test_try_run_refuses_community_route_without_optin(monkeypatch) -> None:
    monkeypatch.setenv("LUMOGIS_TOOL_CATALOG_ENABLED", "true")
    monkeypatch.delenv("LUMOGIS_ALLOW_UNCONTAINED_COMMUNITY_CAPABILITIES", raising=False)
    route = OopCapabilityToolRoute(
        base_url="http://community:9",
        capability_id="community",
        tool_name="community.ping",
        connector="capability.community",
        action_type="community.ping",
        is_write=False,
        require_bearer=True,
        get_bearer=lambda: "t",
        is_community=True,
    )
    tok = OOP_TOOL_ROUTES.set({"community.ping": route})
    try:
        out = try_run_oop_capability_tool("community.ping", {}, user_id="u1")
    finally:
        OOP_TOOL_ROUTES.reset(tok)
    assert out is not None
    import json

    payload = json.loads(out)
    assert payload["error"] == "tool-unavailable"
    assert payload["reason"] == "community_capability_uncontained"


# --------------------------------------------------------------------------
# Fix #1 — first-party wins a tool-name collision (not the community service)
# --------------------------------------------------------------------------


def _registered_named_tool(cap_id: str, tool_name: str, *, is_community: bool) -> RegisteredService:
    m = _manifest(cap_id)
    tool = m.tools[0].model_copy(update={"name": tool_name})
    return RegisteredService(
        manifest=m.model_copy(update={"tools": [tool]}),
        base_url=f"http://{cap_id}:9",
        registered_at=datetime.now(timezone.utc),
        healthy=True,
        is_community=is_community,
    )


def test_first_party_wins_tool_name_collision(monkeypatch) -> None:
    monkeypatch.delenv("LUMOGIS_ALLOW_UNCONTAINED_COMMUNITY_CAPABILITIES", raising=False)
    # Community id ("aaa") sorts before the first-party id ("zzz"): without the
    # is_community-first sort, the community service would claim the shared name.
    reg = _FakeReg(
        _registered_named_tool("aaa-community", "shared.tool", is_community=True),
        _registered_named_tool("zzz-first-party", "shared.tool", is_community=False),
    )
    monkeypatch.setenv("LUMOGIS_CAPABILITY_BEARER_AAA_COMMUNITY", "t")
    monkeypatch.setenv("LUMOGIS_CAPABILITY_BEARER_ZZZ_FIRST_PARTY", "t")
    routes, extra_defs = _collect_oop_eligible(reg, set())
    assert routes["shared.tool"].is_community is False  # first-party claimed it
    assert routes["shared.tool"].capability_id == "zzz-first-party"
    assert "shared.tool" in {d["name"] for d in extra_defs}  # visible to the LLM


# --------------------------------------------------------------------------
# Fix #2/#3 — a first-party service reclaims its id from a shadow / origin move
# --------------------------------------------------------------------------


@pytest.fixture()
def _pinned_first_party(tmp_path, monkeypatch):
    import services.capability_egress as egress

    f = tmp_path / "fp.txt"
    f.write_text("real-svc  http://real-svc:9\n", encoding="utf-8")
    monkeypatch.setenv("LUMOGIS_FIRST_PARTY_CAPABILITIES_FILE", str(f))
    egress._first_party_cache = None  # force reload from our pinned file
    yield
    egress._first_party_cache = None  # don't leak into other tests


async def test_first_party_reclaims_id_from_shadow(_pinned_first_party) -> None:
    # A shadow answers first with id=real-svc from the WRONG origin → community.
    reg = CapabilityRegistry(transport=httpx.MockTransport(_serve(_manifest("real-svc"))))
    await reg.discover(["http://shadow:9"])
    assert reg.get_service("real-svc").is_community is True
    assert reg.get_service("real-svc").base_url == "http://shadow:9"
    # The genuine service at its PINNED origin reclaims the id (first-party wins).
    reg._transport = httpx.MockTransport(_serve(_manifest("real-svc")))
    await reg.discover(["http://real-svc:9"])
    svc = reg.get_service("real-svc")
    assert svc.is_community is False
    assert svc.base_url == "http://real-svc:9"


async def test_community_cannot_displace_existing_from_other_origin(_pinned_first_party) -> None:
    # A first-party registration must not be displaceable by a community response
    # from a different origin (shadow-takeover guard stays intact).
    reg = CapabilityRegistry(transport=httpx.MockTransport(_serve(_manifest("real-svc"))))
    await reg.discover(["http://real-svc:9"])
    assert reg.get_service("real-svc").is_community is False
    reg._transport = httpx.MockTransport(_serve(_manifest("real-svc")))
    await reg.discover(["http://shadow:9"])  # community, different origin → refused
    svc = reg.get_service("real-svc")
    assert svc.is_community is False
    assert svc.base_url == "http://real-svc:9"
