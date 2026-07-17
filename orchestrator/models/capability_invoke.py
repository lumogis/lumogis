# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Capability invoke contract v1 — wire envelopes (LUM-41).

The versioned request/response envelopes Lumogis Core exchanges with an
out-of-process capability service when invoking a tool. These are the ABIs
external plugin authors build to (LUM-241 author guide, LUM-171 marketplace),
so field names are load-bearing: any rename/removal is a MAJOR contract bump
and any additive field is at least a MINOR bump (see ``CONTRACT_VERSION``).

Shapes:

  request  = {"contract_version": "1.0", "tool": "<name>",
              "arguments": {...}, "meta": {"user": "<attr>", "request_id": "<uuid>"}}
  response = {"ok": true, "output": <any-json>}                # success
           | {"ok": false, "error": {"code", "message", "retryable"}}  # failure

``meta.user`` is attribution only (mirrors the ``X-Lumogis-User`` header) and
MUST NOT be used by a capability for authorization or data scoping. A tool that
scopes per user reads ``arguments.user_id`` — a functional argument Core injects
alongside ``meta.user``.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel
from pydantic import Field
from pydantic import model_validator

CONTRACT_VERSION = "1.0"


class InvokeErrorCode(str, Enum):
    """Fixed structured-error vocabulary surfaced to the LLM.

    ``invalid_output`` is Core-side only: the capability returned ``ok:true``
    but the ``output`` failed the tool's declared ``output_schema`` (or exceeded
    the output byte cap). Every other code may originate at the capability or be
    mapped by Core from a transport failure.
    """

    invalid_arguments = "invalid_arguments"
    unauthorized = "unauthorized"
    not_found = "not_found"
    timeout = "timeout"
    unavailable = "unavailable"
    invalid_output = "invalid_output"
    internal = "internal"


class CapabilityInvokeMeta(BaseModel):
    """Envelope metadata that never collides with a tool's own arguments."""

    user: str
    """Attribution only (mirrors ``X-Lumogis-User``). NOT authentication or scoping."""
    request_id: str
    """A per-invoke uuid4 for correlating logs/audit across Core and the service."""


class CapabilityInvokeRequest(BaseModel):
    """The wrapped request Core POSTs to a capability tool's invoke path."""

    contract_version: str = CONTRACT_VERSION
    tool: str
    """LLM-facing tool name — may differ from the HTTP route (``invoke.path``)."""
    arguments: dict[str, Any] = Field(default_factory=dict)
    meta: CapabilityInvokeMeta


class CapabilityInvokeError(BaseModel):
    """Structured failure detail inside a ``{"ok": false}`` response."""

    code: InvokeErrorCode
    message: str
    retryable: bool = False


class CapabilityInvokeResponse(BaseModel):
    """The wrapped response a capability returns from an invoke.

    Exactly one branch is populated, keyed off ``ok``: on success ``error`` is
    ``None`` and ``output`` carries any JSON value (**including** ``null`` — a
    tool legitimately returning null/[]/0 must not be misread as a failure); on
    failure ``error`` is present. Do NOT gate success on ``output`` truthiness.
    """

    ok: bool
    output: Any | None = None
    error: CapabilityInvokeError | None = None

    @model_validator(mode="after")
    def _check_branch(self) -> CapabilityInvokeResponse:
        if self.ok:
            if self.error is not None:
                raise ValueError("ok=true response must not carry an error")
        else:
            if self.error is None:
                raise ValueError("ok=false response must carry an error")
        return self
