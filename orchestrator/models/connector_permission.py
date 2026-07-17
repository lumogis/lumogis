# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Pydantic models for the per-user connector permission surface.

Per plan ``per_user_connector_permissions`` (Audit A2 closure):

* :class:`InternalConnectorPermission` is the service-layer row shape;
  it never reaches the wire directly.
* :class:`ConnectorPermissionPublic` is the user-facing wire shape
  returned by ``/api/v1/me/permissions`` GET/PUT/DELETE handlers.
* :class:`ConnectorPermissionAdminView` is the admin enumeration shape
  surfaced by ``/api/v1/admin/permissions`` and the per-user admin
  inspection routes; it adds owner identity (``user_id``, ``email``).
* :class:`ConnectorPermissionUpdate` is the PUT request body. Mirrors
  the ``extra='forbid'`` discipline established by ``models/mcp_token``
  (D4 / D16): unknown fields rejected with HTTP 422 so a typo in the
  body shape can never silently no-op.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from typing import Literal

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field

Mode = Literal["ASK", "DO"]


def _flatten_optional_null_anyof(json_schema: dict[str, Any]) -> dict[str, Any]:
    """Keep optional PUT fields as plain schemas for OpenAPI/oasdiff parity.

    Pydantic emits ``anyOf: [{enum…}, {type: null}]`` for ``Mode | None``; oasdiff
    then reports ``request-property-enum-value-removed`` vs the prior required
    ``mode`` enum even though ASK/DO are still accepted. Flatten to the non-null
    branch and rely on the field not being listed in ``required``.
    """
    props = json_schema.get("properties") or {}
    for key in ("mode", "scopes"):
        prop = props.get(key)
        if not isinstance(prop, dict) or "anyOf" not in prop:
            continue
        chosen: dict[str, Any] | None = None
        for branch in prop["anyOf"]:
            if branch.get("type") == "null":
                continue
            chosen = dict(branch)
            break
        if chosen is None:
            continue
        if title := prop.get("title"):
            chosen["title"] = title
        if description := prop.get("description"):
            chosen["description"] = description
        props[key] = chosen
    return json_schema


class InternalConnectorPermission(BaseModel):
    """Service-layer row shape. Never reaches the wire directly."""

    id: int
    user_id: str
    connector: str
    mode: Mode
    created_at: datetime
    updated_at: datetime


class ConnectorPermissionPublic(BaseModel):
    """User-facing wire shape. Stable contract."""

    connector: str
    mode: Mode
    scopes: list[str] = Field(
        default_factory=list,
        description="Granted permission scopes for this connector (LUM-612).",
    )
    is_default: bool = Field(
        ...,
        description=(
            "True when no per-user row exists and the response reflects "
            "the _DEFAULT_MODE='ASK' fallback."
        ),
    )
    updated_at: datetime | None = Field(
        ...,
        description="None when is_default=True (no row to time-stamp).",
    )


class ConnectorPermissionAdminView(BaseModel):
    """Admin enumeration shape — adds owner identity."""

    user_id: str
    email: str | None = Field(
        ...,
        description=(
            "None when the row is for a deleted user (forensic retention). "
            "Populated for disabled-but-extant users."
        ),
    )
    connector: str
    mode: Mode
    scopes: list[str] = Field(
        default_factory=list,
        description="Granted permission scopes for this connector (LUM-612).",
    )
    is_default: bool
    updated_at: datetime | None


class ConnectorPermissionUpdate(BaseModel):
    """Request body for PUT routes.

    ``extra='forbid'`` mirrors ``mcp_token_user_map`` D4/D16: a typo
    like ``{"Mode": "DO"}`` (capital M) is rejected with 422 instead of
    silently no-opping.

    LUM-612: both fields are optional. ``mode`` present replaces the Ask/Do
    mode; ``scopes`` present replaces the granted set. A field left ``None`` is
    untouched (a mode-only PUT does not wipe scopes and vice-versa); an empty
    body ``{}`` is a no-op.
    """

    model_config = ConfigDict(extra="forbid")
    mode: Mode | None = None
    scopes: list[str] | None = None

    @classmethod
    def __get_pydantic_json_schema__(cls, core_schema: Any, handler: Any) -> dict[str, Any]:
        return _flatten_optional_null_anyof(handler(core_schema))


class CapabilityGrantView(BaseModel):
    """Consent surface: what a capability requires vs what the user granted."""

    capability_id: str
    connector: str
    required: list[str]
    granted: list[str]
    missing: list[str]
    risk_tier: str | None = None
