# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""FU-3: per-user ``permission_mode`` on unified tool catalog / me/tools read model."""

from __future__ import annotations

from models.tool_spec import ToolSpec
from services.capability_registry import CapabilityRegistry
from services.unified_tools import build_tool_catalog_for_user

from services import tools as services_tools


def _noop_handler(_input: dict, *, user_id: str) -> str:
    del user_id
    return "{}"


def test_permission_mode_do_when_connector_in_do_mode(monkeypatch) -> None:
    monkeypatch.setattr("permissions.get_connector_mode", lambda **kw: "DO")
    cat = build_tool_catalog_for_user(
        "alice",
        capability_registry=CapabilityRegistry(),
        list_actions_fn=lambda: [],
    )
    sf = next(e for e in cat.entries if e.name == "search_files")
    assert sf.connector == "filesystem-mcp"
    assert sf.permission_mode == "do"


def test_permission_mode_ask_for_read_tool_under_ask(monkeypatch) -> None:
    monkeypatch.setattr("permissions.get_connector_mode", lambda **kw: "ASK")
    cat = build_tool_catalog_for_user(
        "bob",
        capability_registry=CapabilityRegistry(),
        list_actions_fn=lambda: [],
    )
    sf = next(e for e in cat.entries if e.name == "search_files")
    assert sf.is_write is False
    assert sf.permission_mode == "ask"


def test_permission_mode_blocked_for_write_tool_under_ask(monkeypatch) -> None:
    specs = list(services_tools.TOOL_SPECS)
    specs.append(
        ToolSpec(
            name="catalog.test.write",
            connector="test-write-connector",
            action_type="test_write",
            is_write=True,
            definition={
                "name": "catalog.test.write",
                "description": "Test write row",
                "parameters": {"type": "object", "properties": {}},
            },
            handler=_noop_handler,
        )
    )
    monkeypatch.setattr("permissions.get_connector_mode", lambda **kw: "ASK")
    cat = build_tool_catalog_for_user(
        "u1",
        tool_specs=specs,
        capability_registry=CapabilityRegistry(),
        list_actions_fn=lambda: [],
    )
    row = next(e for e in cat.entries if e.name == "catalog.test.write")
    assert row.permission_mode == "blocked"


def test_permission_mode_unknown_without_connector(monkeypatch) -> None:
    monkeypatch.setattr("permissions.get_connector_mode", lambda **kw: "DO")
    cat = build_tool_catalog_for_user(
        "u2",
        capability_registry=CapabilityRegistry(),
        list_actions_fn=lambda: [],
    )
    mcp = [e for e in cat.entries if e.source == "mcp"]
    assert mcp
    assert all(e.permission_mode == "unknown" for e in mcp)


def test_capability_row_resolves_permission_connector(monkeypatch) -> None:
    from datetime import datetime
    from datetime import timezone

    from models.capability import CapabilityLicenseMode
    from models.capability import CapabilityManifest
    from models.capability import CapabilityMaturity
    from models.capability import CapabilityTool
    from models.capability import CapabilityTransport
    from services.capability_registry import RegisteredService

    def _ct(name: str) -> CapabilityTool:
        return CapabilityTool(
            name=name,
            description="t",
            license_mode=CapabilityLicenseMode.COMMUNITY,
            input_schema={"type": "object"},
            output_schema={"type": "object"},
        )

    m = CapabilityManifest(
        name="svc.x",
        id="com.vendor.svc",
        version="1.0.0",
        type="service",
        transport=CapabilityTransport.HTTP,
        license_mode=CapabilityLicenseMode.COMMUNITY,
        maturity=CapabilityMaturity.PREVIEW,
        description="d",
        tools=[_ct("vendor.tool.one")],
        health_endpoint="/health",
        capabilities_endpoint="/capabilities",
        permissions_required=["custom.connector"],
        config_schema={"type": "object"},
        min_core_version="0.3.0rc1",
        maintainer="t",
    )

    class _Reg:
        def all_services(self):
            return [
                RegisteredService(
                    manifest=m,
                    base_url="http://cap:1",
                    registered_at=datetime.now(timezone.utc),
                    healthy=True,
                )
            ]

    seen: list[tuple[str, str]] = []

    def _track(*, user_id: str, connector: str) -> str:
        seen.append((user_id, connector))
        return "ASK"

    monkeypatch.setattr("permissions.get_connector_mode", _track)
    cat = build_tool_catalog_for_user(
        "grace",
        capability_registry=_Reg(),
        list_actions_fn=lambda: [],
    )
    row = next(e for e in cat.entries if e.name == "vendor.tool.one")
    assert row.connector == "custom.connector"
    assert ("grace", "custom.connector") in seen
    assert row.permission_mode == "ask"


def test_permission_mode_unknown_when_lookup_raises(monkeypatch) -> None:
    def _boom(**_kw):
        raise RuntimeError("db down")

    monkeypatch.setattr("permissions.get_connector_mode", _boom)
    cat = build_tool_catalog_for_user(
        "u3",
        capability_registry=CapabilityRegistry(),
        list_actions_fn=lambda: [],
    )
    sf = next(e for e in cat.entries if e.name == "search_files")
    assert sf.permission_mode == "unknown"


def test_injected_get_connector_mode_fn() -> None:
    cat = build_tool_catalog_for_user(
        "z",
        get_connector_mode_fn=lambda **kw: "DO",
        capability_registry=CapabilityRegistry(),
        list_actions_fn=lambda: [],
    )
    q = next(e for e in cat.entries if e.name == "query_entity")
    assert q.permission_mode == "do"
