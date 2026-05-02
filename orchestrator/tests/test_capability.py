# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Contract tests for the capability manifest schema (Area 1).

These tests lock the wire format that every out-of-process capability service
must conform to. They also lock the Core version constant in place so Area 2
can rely on it for `min_core_version` compatibility checks.
"""

import pytest
from pydantic import ValidationError

from models.capability import CapabilityLicenseMode
from models.capability import CapabilityManifest
from models.capability import CapabilityMaturity
from models.capability import CapabilityTool
from models.capability import CapabilityTransport


def _sample_tool(
    name: str = "memory.search",
    license_mode: CapabilityLicenseMode = CapabilityLicenseMode.COMMUNITY,
) -> CapabilityTool:
    return CapabilityTool(
        name=name,
        description="Semantic search over past sessions.",
        license_mode=license_mode,
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer", "default": 5},
            },
            "required": ["query"],
        },
        output_schema={
            "type": "object",
            "properties": {"results": {"type": "array"}},
        },
    )


def _sample_manifest(**overrides) -> CapabilityManifest:
    base = dict(
        name="lumogis-memory-pro",
        id="lumogis.memory.pro",
        version="0.1.0",
        type="service",
        transport=CapabilityTransport.HTTP,
        license_mode=CapabilityLicenseMode.COMMERCIAL,
        maturity=CapabilityMaturity.PREVIEW,
        description="Premium memory capability service.",
        tools=[
            _sample_tool(),
            _sample_tool(name="memory.summarize", license_mode=CapabilityLicenseMode.COMMERCIAL),
        ],
        health_endpoint="/health",
        capabilities_endpoint="/capabilities",
        permissions_required=["memory:read", "memory:write"],
        config_schema={
            "type": "object",
            "properties": {"retention_days": {"type": "integer"}},
        },
        min_core_version="0.3.0rc1",
        maintainer="lumogis",
    )
    base.update(overrides)
    return CapabilityManifest(**base)


def test_manifest_roundtrip_json():
    manifest = _sample_manifest()
    raw = manifest.model_dump_json()
    restored = CapabilityManifest.model_validate_json(raw)

    assert restored == manifest
    assert restored.id == "lumogis.memory.pro"
    assert restored.transport is CapabilityTransport.HTTP
    assert restored.license_mode is CapabilityLicenseMode.COMMERCIAL
    assert restored.maturity is CapabilityMaturity.PREVIEW
    assert len(restored.tools) == 2
    assert restored.tools[0].license_mode is CapabilityLicenseMode.COMMUNITY
    assert restored.tools[1].license_mode is CapabilityLicenseMode.COMMERCIAL
    assert restored.tools[0].input_schema["required"] == ["query"]


def test_capability_tool_roundtrip():
    tool = _sample_tool()
    restored = CapabilityTool.model_validate_json(tool.model_dump_json())

    assert restored == tool
    assert restored.license_mode is CapabilityLicenseMode.COMMUNITY
    assert restored.input_schema["properties"]["limit"]["default"] == 5


def test_manifest_rejects_invalid_transport():
    with pytest.raises(ValidationError):
        _sample_manifest(transport="grpc")


def test_manifest_rejects_invalid_type_literal():
    with pytest.raises(ValidationError):
        _sample_manifest(type="garbage")


def test_manifest_requires_tools_field():
    payload = _sample_manifest().model_dump(mode="json")
    payload.pop("tools")

    with pytest.raises(ValidationError):
        CapabilityManifest.model_validate(payload)


def test_manifest_accepts_empty_tools_list():
    """A registered service with zero tools is valid (e.g. an adapter that
    only contributes config or health surface). The contract requires the
    field to be present, not non-empty."""
    manifest = _sample_manifest(tools=[])
    restored = CapabilityManifest.model_validate_json(manifest.model_dump_json())
    assert restored.tools == []


def test_management_url_optional_and_excluded_when_none():
    """A manifest without `management_url` validates and serialises cleanly.

    `exclude_none=True` MUST omit the key entirely so older clients that
    don't know about the field never see a `null` they have to handle.
    """
    manifest = _sample_manifest()
    assert manifest.management_url is None

    dumped = manifest.model_dump(mode="json", exclude_none=True)
    assert "management_url" not in dumped


def test_management_url_set_roundtrips():
    """When set, `management_url` survives a JSON round trip and lands on
    the deserialised model with the exact string value."""
    url = "https://lumogis.example.com/graph/mgm"
    manifest = _sample_manifest(management_url=url)
    restored = CapabilityManifest.model_validate_json(manifest.model_dump_json())

    assert restored.management_url == url


def test_management_url_present_in_default_dump():
    """Without `exclude_none=True`, the field appears as null so explicit
    consumers can distinguish 'unset' from 'absent'."""
    manifest = _sample_manifest()
    dumped = manifest.model_dump(mode="json")
    assert "management_url" in dumped
    assert dumped["management_url"] is None


def test_core_version_constant_importable():
    """Locks orchestrator/__version__.py in place for Area 2's compatibility
    check against incoming manifests' `min_core_version`."""
    from __version__ import __version__

    assert isinstance(__version__, str)
    assert __version__
