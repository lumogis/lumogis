# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Capability manifest schema.

Defines the contract every out-of-process capability service exposes at its
`/capabilities` endpoint. Lumogis Core reads this manifest to discover what
a service offers, what licence tier it sits in, and how to call it.

This module is the foundation for Area 2 (service discovery and registration)
and Area 4 (Core's own self-describing manifest exposed at GET /capabilities).
"""

from enum import Enum
from typing import Any
from typing import Literal

from pydantic import BaseModel
from pydantic import Field


class CapabilityTransport(str, Enum):
    HTTP = "http"
    MCP = "mcp"


class CapabilityLicenseMode(str, Enum):
    COMMUNITY = "community"
    COMMERCIAL = "commercial"


class CapabilityMaturity(str, Enum):
    EXPERIMENTAL = "experimental"
    PREVIEW = "preview"
    STABLE = "stable"


class CapabilityTool(BaseModel):
    """A single tool exposed by a capability service.

    `input_schema` and `output_schema` are JSON Schema documents. They are
    intentionally untyped (`dict[str, Any]`) at this layer — manifest
    authors carry full JSON Schema flexibility and Core does not validate
    schema correctness until a tool is actually invoked.
    """

    name: str
    description: str
    license_mode: CapabilityLicenseMode
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]


class CapabilityManifest(BaseModel):
    """Top-level descriptor returned by a capability service's /capabilities endpoint.

    Identity:
        `id` is the stable identifier used by the registry to deduplicate
        services across discovery passes. `name` is human-readable.

    Compatibility:
        `min_core_version` is compared against orchestrator/__version__.py
        during registration (Area 2).

    Endpoints:
        `health_endpoint` is the path Core probes for liveness (relative to
        the service base URL).

        `capabilities_endpoint` is **documentary in v1**: Lumogis Core
        always discovers manifests via hardcoded ``GET {base_url}/capabilities``
        (see :mod:`services.capability_registry`). Manifests SHOULD set this
        field to ``/capabilities``; other values do not change discovery.
    """

    name: str
    id: str
    version: str
    type: Literal["service", "plugin", "adapter"]
    transport: CapabilityTransport
    license_mode: CapabilityLicenseMode
    maturity: CapabilityMaturity
    description: str
    tools: list[CapabilityTool]
    health_endpoint: str
    capabilities_endpoint: str = Field(
        description=(
            "Documentary path for tooling and authors. v1 Core ignores this for "
            "discovery and always requests GET {base_url}/capabilities. Use "
            "/capabilities."
        ),
    )
    permissions_required: list[str]
    config_schema: dict[str, Any]
    min_core_version: str
    maintainer: str
    management_url: str | None = None
    """Optional absolute URL the operator's browser can reach to administer
    the service (e.g. an /mgm page). When `None`, the service has no
    operator-facing UI. When set, MUST be an absolute URL — not a relative
    path — because external clients (Core's status page, future MCP
    marketplaces) resolve it relative to their own origin, not the
    capability service's container hostname. Backward-compatible: existing
    manifests without this field validate unchanged.
    """
