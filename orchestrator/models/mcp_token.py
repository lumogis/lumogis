# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Pydantic models for the per-user MCP token surface.

Three layers of token representation, deliberately separate so handlers
can never serialise the SHA-256 hash or the lookup prefix to a client by
accident. Mirrors the ``models/auth.py`` split (``InternalUser`` /
``UserAdminView`` / ``UserPublic``) — see plan ``mcp_token_user_map`` D15.

Layers
------
* ``InternalMcpToken``  : every column including ``token_hash`` and
                          ``token_prefix``. Server-internal only; never
                          serialised to a route response.
* ``McpTokenAdminView`` : admin-only listing shape. Adds ``user_id``;
                          still excludes ``token_hash`` and ``token_prefix``.
* ``McpTokenPublic``    : safe response shape returned by every
                          ``/api/v1/me/mcp-tokens`` route. No hash, no
                          prefix, no plaintext.

Plaintext lives in exactly one place: ``MintMcpTokenResponse.plaintext``,
returned exactly once at mint time. Per D15 it is declared with
``Field(repr=False)`` so a stray ``%r`` log does not leak it.

D16: ``MintMcpTokenRequest.model_config = ConfigDict(extra="forbid")``
defends D4 — ``expires_at`` (and any other unknown field) is rejected
with HTTP 422 rather than silently accepted-and-discarded by Pydantic v2's
default "ignore extras" posture.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class InternalMcpToken(BaseModel):
    """Server-side full token row. Never serialised to a client.

    ``token_prefix`` is non-secret (the indexed lookup handle) but is
    redacted from ``repr()`` per D15 so a stray ``log("%r", token)``
    does not leak it into operator logs alongside any context. The
    secret is the SHA-256 hex in ``token_hash`` — also ``repr=False``.
    """

    id: str
    user_id: str
    token_prefix: str = Field(repr=False)
    token_hash: str = Field(repr=False)
    label: str
    scopes: list[str] | None
    created_at: datetime
    last_used_at: datetime | None = None
    expires_at: datetime | None = None
    revoked_at: datetime | None = None


class McpTokenPublic(BaseModel):
    """Safe response shape returned by every ``/api/v1/me/mcp-tokens`` route.

    Strictly excludes ``token_hash``, ``token_prefix`` and the plaintext
    bearer (the latter only ever appears in :class:`MintMcpTokenResponse`).
    """

    id: str
    label: str
    scopes: list[str] | None
    created_at: datetime
    last_used_at: datetime | None = None
    expires_at: datetime | None = None
    revoked_at: datetime | None = None


class McpTokenAdminView(McpTokenPublic):
    """Admin-only listing shape: ``McpTokenPublic`` + the owner ``user_id``.

    Returned by ``GET /api/v1/admin/users/{user_id}/mcp-tokens`` and the
    admin DELETE route's response body.
    """

    user_id: str


class MintMcpTokenRequest(BaseModel):
    """Body for ``POST /api/v1/me/mcp-tokens``.

    Strict — D4 promises ``expires_at`` is rejected, not silently dropped.
    Without ``ConfigDict(extra="forbid")`` Pydantic v2 would ignore the
    field by default and contradict the contract (D16).
    """

    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1, max_length=64)


class MintMcpTokenResponse(BaseModel):
    """Body of ``POST /api/v1/me/mcp-tokens``'s 201 response.

    The ``plaintext`` field is the ONE-AND-ONLY place the bearer is ever
    surfaced over HTTP. After this response, the operator has lost their
    chance to recover it — the server only stores SHA-256.

    ``Field(repr=False)`` defends against stray ``%r`` log lines (D15).
    No FastAPI middleware in v1 logs response bodies, so this redaction
    is the second line of defence; if a future middleware change adds
    body logging, the maintainer MUST explicitly carve out this model
    (see plan §"Security decisions" #4).
    """

    token: McpTokenPublic
    plaintext: str = Field(repr=False)
