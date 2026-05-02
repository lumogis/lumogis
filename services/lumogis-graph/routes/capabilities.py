# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""KG service `/capabilities` endpoint.

Returns the static `CapabilityManifest` describing this service. The manifest
is built once at module import (it does not depend on per-request state) so
the endpoint is essentially a constant-time JSON dump.

Field rules per the extraction plan §"Manifest detail":

  - `id` MUST equal `"lumogis-graph"` — Core's CapabilityRegistry uses this
    as the dedup key across discovery passes.
  - `version` MUST track `__version__.__version__`.
  - `min_core_version` is `"0.3.0rc1"`. CRITICAL: NOT `"0.3.0"`. Core's
    `services/capability_registry.py` does the comparison via
    `packaging.version.Version`, where `Version("0.3.0rc1") < Version("0.3.0")`
    evaluates to True (rc is a pre-release). If we said `"0.3.0"` here,
    every Core instance still on the `0.3.0rc*` line would be marked
    INCOMPATIBLE and KG would silently fail registry discovery.
  - `management_url` is the ABSOLUTE URL operators reach for the /mgm
    page. The default `http://lumogis-graph:8001/mgm` is in-network only
    (Docker bridge); deployments behind a reverse proxy MUST override
    `KG_MANAGEMENT_URL` to the externally resolvable URL. We log a
    startup WARNING from `main.py` if the value looks like the default
    AND any of `LUMOGIS_PUBLIC_HOSTNAME`, `LUMOGIS_BEHIND_PROXY=true`,
    or a non-loopback `EXTERNAL_BASE_URL` is set.
  - `tools[]` order matches Pass 2 step 10 implementation order.
"""

import logging
import os

from fastapi import APIRouter

from __version__ import __version__
from models.capability import (
    CapabilityLicenseMode,
    CapabilityManifest,
    CapabilityMaturity,
    CapabilityTool,
    CapabilityTransport,
)

router = APIRouter()
_log = logging.getLogger(__name__)


_MIN_CORE_VERSION = "0.3.0rc1"


def _kg_base_url() -> str:
    """Default in-network URL Core uses to reach this service.

    Read at import time so the manifest is a constant. The default matches
    `docker-compose.premium.yml` (service name `lumogis-graph`, port 8001).
    Operators MUST override `KG_BASE_URL` if they rename the service or
    expose it on a non-default port.
    """
    return os.environ.get("KG_BASE_URL", "http://lumogis-graph:8001").rstrip("/")


def _kg_management_url() -> str:
    """Operator-browser-reachable URL for the /mgm page.

    Defaults to the in-network base URL + /mgm. This is reachable ONLY from
    inside the Docker bridge network; production deployments behind a
    reverse proxy MUST override `KG_MANAGEMENT_URL`.
    """
    return os.environ.get("KG_MANAGEMENT_URL", f"{_kg_base_url()}/mgm")


# ---------------------------------------------------------------------------
# Tool descriptors. Schemas are JSON Schema documents; manifest authors keep
# full flexibility because Core's CapabilityRegistry does not validate schema
# correctness until a tool is actually invoked.
# ---------------------------------------------------------------------------

_TOOLS: list[CapabilityTool] = [
    CapabilityTool(
        name="graph.query_ego",
        description="Return the n-hop ego subgraph around an entity.",
        license_mode=CapabilityLicenseMode.COMMERCIAL,
        input_schema={
            "type": "object",
            "properties": {
                "entity": {"type": "string"},
                "max_depth": {"type": "integer", "minimum": 1, "maximum": 4, "default": 2},
                "user_id": {"type": "string", "default": "default"},
            },
            "required": ["entity"],
        },
        output_schema={
            "type": "object",
            "properties": {"output": {"type": "string"}},
            "required": ["output"],
        },
    ),
    CapabilityTool(
        name="graph.query_path",
        description="Shortest path between two entities, max depth K.",
        license_mode=CapabilityLicenseMode.COMMERCIAL,
        input_schema={
            "type": "object",
            "properties": {
                "from": {"type": "string"},
                "to": {"type": "string"},
                "max_depth": {"type": "integer", "minimum": 1, "maximum": 4, "default": 4},
                "user_id": {"type": "string", "default": "default"},
            },
            "required": ["from", "to"],
        },
        output_schema={
            "type": "object",
            "properties": {"output": {"type": "string"}},
            "required": ["output"],
        },
    ),
    CapabilityTool(
        name="graph.query_mentions",
        description="Documents/sessions mentioning an entity.",
        license_mode=CapabilityLicenseMode.COMMERCIAL,
        input_schema={
            "type": "object",
            "properties": {
                "entity": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
                "user_id": {"type": "string", "default": "default"},
            },
            "required": ["entity"],
        },
        output_schema={
            "type": "object",
            "properties": {"output": {"type": "string"}},
            "required": ["output"],
        },
    ),
    CapabilityTool(
        name="graph.get_context",
        description="Build a `[Graph]` context fragment for a query.",
        license_mode=CapabilityLicenseMode.COMMERCIAL,
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "user_id": {"type": "string", "default": "default"},
                "max_fragments": {"type": "integer", "minimum": 1, "maximum": 16, "default": 5},
            },
            "required": ["query"],
        },
        output_schema={
            "type": "object",
            "properties": {"fragments": {"type": "array", "items": {"type": "string"}}},
            "required": ["fragments"],
        },
    ),
    CapabilityTool(
        name="graph.backfill",
        description="Trigger a partial graph backfill.",
        license_mode=CapabilityLicenseMode.COMMERCIAL,
        input_schema={
            "type": "object",
            "properties": {
                "limit_per_type": {"type": ["integer", "null"], "minimum": 1, "default": None},
            },
        },
        output_schema={
            "type": "object",
            "properties": {"status": {"type": "string"}},
            "required": ["status"],
        },
    ),
    CapabilityTool(
        name="graph.health",
        description="Detailed graph health (counts, last-reconciled).",
        license_mode=CapabilityLicenseMode.COMMERCIAL,
        input_schema={"type": "object", "properties": {}},
        output_schema={
            "type": "object",
            "properties": {
                "duplicate_candidate_count": {"type": "integer"},
                "orphan_entity_pct": {"type": "number"},
                "mean_entity_completeness": {"type": "number"},
                "constraint_violation_counts": {"type": "object"},
                "ingestion_quality_trend_7d": {"type": ["number", "null"]},
                "temporal_freshness": {"type": "object"},
            },
        },
    ),
]


def build_manifest() -> CapabilityManifest:
    """Build the static `CapabilityManifest` for this service."""
    return CapabilityManifest(
        id="lumogis-graph",
        name="Lumogis Graph Pro",
        version=__version__,
        type="service",
        transport=CapabilityTransport.HTTP,
        license_mode=CapabilityLicenseMode.COMMERCIAL,
        maturity=CapabilityMaturity.EXPERIMENTAL,
        description=(
            "Out-of-process knowledge-graph capability service for Lumogis. "
            "Owns all FalkorDB writes; replays missed projections via a daily "
            "Postgres reconciliation pass; exposes six graph.* tools over /mcp."
        ),
        tools=_TOOLS,
        health_endpoint="/health",
        capabilities_endpoint="/capabilities",
        permissions_required=[],
        config_schema={"type": "object", "properties": {}},
        min_core_version=_MIN_CORE_VERSION,
        maintainer="Lumogis",
        management_url=_kg_management_url(),
    )


_MANIFEST: CapabilityManifest = build_manifest()


@router.get("/capabilities")
def get_capabilities() -> dict:
    """Return the static `CapabilityManifest` JSON for this service."""
    return _MANIFEST.model_dump(mode="json")
