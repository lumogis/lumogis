# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Capability manifest schema.

Defines the contract every out-of-process capability service exposes at its
`/capabilities` endpoint. Lumogis Core reads this manifest to discover what
a service offers, what licence tier it sits in, and how to call it.

This module is the foundation for Area 2 (service discovery and registration)
and Area 4 (Core's own self-describing manifest exposed at GET /capabilities).
"""

import re
from enum import Enum
from typing import Any
from typing import Literal

from pydantic import BaseModel
from pydantic import Field
from pydantic import field_validator

# LUM-613: declared-egress allowlist bounds. A capability declares the external
# hosts it intends to contact; Core captures this for audit/visibility and (via
# LUM-618) container-network enforcement. Bounds keep a hostile manifest from
# bloating the registry/allowlist.
_MAX_EXTERNAL_ENDPOINTS = 32
_MAX_HOST_LEN = 253  # DNS name max
# Per-label host rule (post-lowercase): each dot-separated label is 1-63 chars,
# alphanumeric, may contain internal hyphens but not lead/trail with one. This
# rejects degenerate forms the old broad `[a-z0-9.-]+` accepted — "."/".."/"-"/
# leading-or-trailing-dot/consecutive-dots/leading-or-trailing-hyphen — so an
# author typo (e.g. ".foo.com", "api..foo") is caught at registration instead of
# validating into a junk allowlist entry that silently never matches. IPv4
# literals still pass (numeric labels match).
_HOST_LABEL_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


def _is_valid_bare_host(host: str) -> bool:
    return bool(host) and all(_HOST_LABEL_RE.match(label) for label in host.split("."))


def normalise_external_endpoints(raw: list[str]) -> list[str]:
    """Validate + normalise a manifest's declared ``external_endpoints`` (LUM-613).

    Accepts bare lowercase hostnames and IPv4 literals only. Rejects (with
    ``ValueError`` → surfaced as a Pydantic ``ValidationError``): schemes,
    paths, ports, wildcards, non-ASCII/IDN, and IPv6 literals — the latter two
    explicitly, not incidentally. Deduplicates (order-preserving) and bounds the
    count and per-host length. IPv6 and ports are both rejected via the ``:``
    check; IDN via the ASCII check.
    """
    if len(raw) > _MAX_EXTERNAL_ENDPOINTS:
        raise ValueError(
            f"external_endpoints declares {len(raw)} hosts; max is {_MAX_EXTERNAL_ENDPOINTS}"
        )
    seen: set[str] = set()
    out: list[str] = []
    for entry in raw:
        if not isinstance(entry, str):
            raise ValueError("external_endpoints entries must be strings")
        host = entry.strip().lower()
        if not host:
            raise ValueError("external_endpoints entry is empty")
        if not host.isascii():
            raise ValueError(f"external_endpoints {entry!r} is non-ASCII/IDN (punycode-encode it)")
        if ":" in host:
            # Rejects both ports (api.foo.com:8443) and IPv6 literals (::1) explicitly.
            raise ValueError(f"external_endpoints {entry!r} contains ':' (no ports/IPv6)")
        if "*" in host:
            raise ValueError(f"external_endpoints {entry!r} uses a wildcard (exact hosts only)")
        if len(host) > _MAX_HOST_LEN:
            raise ValueError(f"external_endpoints entry exceeds {_MAX_HOST_LEN} chars")
        if not _is_valid_bare_host(host):
            raise ValueError(
                f"external_endpoints entry {entry!r} is not a valid bare host or IPv4 literal "
                "(no scheme/path/special chars; no empty, leading/trailing-dot, or "
                "leading/trailing-hyphen labels)"
            )
        if host not in seen:
            seen.add(host)
            out.append(host)
    return out


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


class ToolInvoke(BaseModel):
    """How Core reaches a single tool over HTTP (capability invoke contract v1).

    Decouples the LLM-facing tool ``name`` from the HTTP route: a service may
    map many tools onto one endpoint (or vice-versa). ``method`` is POST-only in
    v1; a GET tool is a v1.1 concern. ``path`` defaults to ``/tools/{name}`` via
    :meth:`CapabilityTool.invoke_path` when unset, preserving the legacy shape.
    """

    method: Literal["POST"] = "POST"
    path: str | None = None


class CapabilityTool(BaseModel):
    """A single tool exposed by a capability service.

    `input_schema` and `output_schema` are JSON Schema documents. They are
    intentionally untyped (`dict[str, Any]`) at this layer — manifest
    authors carry full JSON Schema flexibility. Core validates the invoke
    *output* against `output_schema` when the schema is non-trivial (LUM-41);
    a loosely-typed tool declares a trivial schema (`{"type": "string"}` /
    `{"type": "object"}` / `{}`) which skips validation.
    """

    name: str
    description: str
    license_mode: CapabilityLicenseMode
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    is_write: bool = False
    """Whether invoking this tool mutates state. Feeds permissioning (Ask/Do) and
    risk profiling. Author-declared — Core does not treat it as the sole gate
    (a plugin lying is a LUM-507 sandbox concern)."""
    idempotent: bool = True
    """Whether a retry is safe. Advisory in v1 (retry policy is Core-side)."""
    timeout_ms: int | None = None
    """Requested per-tool invoke budget. Core clamps to a hard ceiling."""
    invoke: ToolInvoke = Field(default_factory=ToolInvoke)
    """Declared HTTP route for this tool. See :meth:`invoke_path`."""

    @property
    def invoke_path(self) -> str:
        """Resolved invoke path: the declared ``invoke.path`` or ``/tools/{name}``.

        A plain property (not a serialised field) so the wire manifest is
        unchanged when a tool relies on the default.
        """
        return self.invoke.path or f"/tools/{self.name}"


class CapabilityAuth(BaseModel):
    """How Core authenticates to a capability service (invoke contract v1).

    ``credential_ref`` is a *key name* (e.g. an env var), never a secret value —
    it lets the manifest declare *which* credential Core resolves, feeding
    LUM-507. ``mode="none"`` invokes without a bearer; permitted in v1 only
    because there are no untrusted external capabilities yet (LUM-507 gates who
    may declare it).
    """

    mode: Literal["bearer", "none"] = "bearer"
    credential_ref: str | None = None


class CapabilityManifest(BaseModel):
    """Top-level descriptor returned by a capability service's /capabilities endpoint.

    Identity:
        `id` is the stable identifier used by the registry to deduplicate
        services across discovery passes. `name` is human-readable.

    Compatibility:
        `min_core_version` is compared against orchestrator/__version__.py
        during registration (Area 2). `contract_version` is the invoke-contract
        version (distinct from the service's own `version`): Core refuses an
        unknown MAJOR and accepts an unknown MINOR (forward-compatible).

    Endpoints:
        `health_endpoint` is the path Core probes for liveness (relative to
        the service base URL).

        `capabilities_endpoint` is **validated, not documentary** (LUM-41):
        Core discovers manifests at the fixed bootstrap path
        ``GET {base_url}/capabilities`` and rejects any manifest whose
        `capabilities_endpoint` disagrees — the field must be correct.
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
            "Discovery path Core requests. Validated against the fixed "
            "GET {base_url}/capabilities bootstrap path; a divergent value is "
            "rejected at registration. Use /capabilities."
        ),
    )
    contract_version: str = "1.0"
    """Capability invoke contract version (MAJOR.MINOR). Unknown MAJOR → refused."""
    auth: CapabilityAuth = Field(default_factory=CapabilityAuth)
    """How Core authenticates to this service's invoke endpoints."""
    permissions_required: list[str]
    external_endpoints: list[str] = Field(default_factory=list)
    """External hosts this capability declares it will contact (LUM-613).

    Optional, additive (recognised at contract 1.1; a 1.0 manifest omitting it
    validates unchanged). Bare lowercase hostnames or IPv4 literals only —
    validated/normalised at parse time. Core reads this for audit/visibility;
    LUM-618 enforces it at the container-network layer. This is a *declaration*,
    not enforcement — Core cannot stop an out-of-process container's egress.
    """

    @field_validator("external_endpoints")
    @classmethod
    def _validate_external_endpoints(cls, v: list[str]) -> list[str]:
        return normalise_external_endpoints(v)

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
