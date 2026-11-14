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

from __version__ import __version__
from fastapi import APIRouter
from models.capability import CapabilityAuth
from models.capability import CapabilityLicenseMode
from models.capability import CapabilityManifest
from models.capability import CapabilityMaturity
from models.capability import CapabilityTool
from models.capability import CapabilityTransport
from models.capability import ToolInvoke

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
# Tool descriptors (capability invoke contract v1 — LUM-41). The manifest MUST
# only advertise tools that resolve to a real endpoint: today the sole invoke
# endpoint is POST /tools/query_graph, so the manifest declares the single
# canonical `graph.query` tool pointing at it via `invoke.path`. The former six
# aspirational graph.* tools (query_ego/path/mentions/get_context/backfill/
# health) had no endpoint and would 404 — they are deferred to a child ticket
# (LUM-78) that grows the real endpoints, then re-adds them here.
# ---------------------------------------------------------------------------

_TOOLS: list[CapabilityTool] = [
    CapabilityTool(
        name="graph.query",
        description=(
            "Query the knowledge graph (ego subgraph, shortest path, or entity "
            "mentions) and return a text context fragment."
        ),
        license_mode=CapabilityLicenseMode.COMMUNITY,
        is_write=False,
        idempotent=True,
        invoke=ToolInvoke(path="/tools/query_graph"),
        input_schema={
            "type": "object",
            "properties": {
                "mode": {"type": "string", "enum": ["ego", "path", "mentions"]},
                "entity": {"type": "string"},
                "max_depth": {"type": "integer", "minimum": 1, "maximum": 4, "default": 2},
                "user_id": {"type": "string", "default": "default"},
            },
            "required": ["mode"],
        },
        # Trivial (any) output schema: query_graph_tool's return shape is
        # polymorphic per mode, so Core does not schema-validate the output.
        output_schema={"type": "object"},
    ),
]


def build_manifest() -> CapabilityManifest:
    """Build the static `CapabilityManifest` for this service."""
    return CapabilityManifest(
        id="lumogis-graph",
        name="Lumogis Graph",
        version=__version__,
        type="service",
        transport=CapabilityTransport.HTTP,
        license_mode=CapabilityLicenseMode.COMMUNITY,
        maturity=CapabilityMaturity.EXPERIMENTAL,
        description=(
            "Out-of-process knowledge-graph capability service for Lumogis. "
            "Owns all FalkorDB writes; replays missed projections via a daily "
            "Postgres reconciliation pass; exposes the graph.query tool."
        ),
        tools=_TOOLS,
        health_endpoint="/health",
        capabilities_endpoint="/capabilities",
        contract_version="1.0",
        auth=CapabilityAuth(mode="bearer", credential_ref="KG_WEBHOOK_SECRET"),
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
