# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""HTTP routes for the per-user MCP token surface.

Per plan ``mcp_token_user_map`` D12, two routers live here:

* ``router``       — user-facing ``/api/v1/me/mcp-tokens`` endpoints
                     gated by :func:`authz.require_user`.
* ``admin_router`` — admin-only ``/api/v1/admin/users/{user_id}/mcp-tokens``
                     endpoints gated by :func:`authz.require_admin`.

Both routers attach :func:`csrf.require_same_origin` to every mutating
verb (D11) — Bearer-authenticated calls bypass the check by design (see
``orchestrator/csrf.py``), so curl + the dashboard's ``fetch`` flows
keep working unchanged.

Response shaping (D15)
----------------------
List + admin-list responses are projected through :class:`McpTokenPublic`
/ :class:`McpTokenAdminView` so ``token_hash`` and ``token_prefix`` can
never reach the wire. The plaintext bearer is returned exactly once, in
:class:`MintMcpTokenResponse.plaintext`, by the mint route.

Audit emission (D14)
--------------------
Each lifecycle handler emits the appropriate ``__mcp_token__.*`` audit
row via :func:`services.mcp_tokens._emit_audit`. The cascade-revoke path
is owned by :func:`services.users.set_disabled` (called from
``routes/admin_users.py``) and is NOT re-emitted here.

Information-leak guard (plan §"Information-leak guard")
-------------------------------------------------------
``DELETE /api/v1/me/mcp-tokens/{token_id}`` returns ``404 Not Found``
when the row exists but belongs to another user. Returning ``403
Forbidden`` would let a non-admin probe whether arbitrary token ids
exist (the response would distinguish "doesn't exist" from "exists but
isn't yours"). The admin DELETE has no analogous concern — it already
asserts ``token.user_id == user_id`` from the path.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status

from auth import get_user
from authz import require_admin, require_user
from csrf import require_same_origin
from models.mcp_token import (
    McpTokenAdminView,
    McpTokenPublic,
    MintMcpTokenRequest,
    MintMcpTokenResponse,
)
from services import mcp_tokens as mcp_tokens_service
from services import users as users_service

_log = logging.getLogger(__name__)


router = APIRouter(
    prefix="/api/v1/me/mcp-tokens",
    tags=["me-mcp-tokens"],
    dependencies=[Depends(require_user)],
)

admin_router = APIRouter(
    prefix="/api/v1/admin/users/{user_id}/mcp-tokens",
    tags=["admin-mcp-tokens"],
    dependencies=[Depends(require_admin)],
)


# ---------------------------------------------------------------------------
# Projections — kept module-private so the route bodies stay one-liners and
# the never-leak invariants (no token_hash, no token_prefix, no plaintext)
# live in exactly one place per layer.
# ---------------------------------------------------------------------------


def _to_public(internal) -> McpTokenPublic:
    return McpTokenPublic(
        id=internal.id,
        label=internal.label,
        scopes=internal.scopes,
        created_at=internal.created_at,
        last_used_at=internal.last_used_at,
        expires_at=internal.expires_at,
        revoked_at=internal.revoked_at,
    )


def _to_admin_view(internal) -> McpTokenAdminView:
    return McpTokenAdminView(
        id=internal.id,
        user_id=internal.user_id,
        label=internal.label,
        scopes=internal.scopes,
        created_at=internal.created_at,
        last_used_at=internal.last_used_at,
        expires_at=internal.expires_at,
        revoked_at=internal.revoked_at,
    )


# ---------------------------------------------------------------------------
# User-facing routes — /api/v1/me/mcp-tokens
# ---------------------------------------------------------------------------


@router.get(
    "",
    response_model=list[McpTokenPublic],
)
def list_my_tokens(
    request: Request,
    include_revoked: bool = False,
) -> list[McpTokenPublic]:
    """List the caller's MCP tokens, newest-created first.

    ``include_revoked=false`` (default) returns only active tokens — the
    common dashboard view. ``include_revoked=true`` includes revoked
    rows so the dashboard's "Revoked" collapsible can populate.
    """
    caller = get_user(request)
    rows = mcp_tokens_service.list_for_user(
        caller.user_id,
        include_revoked=include_revoked,
    )
    return [_to_public(r) for r in rows]


@router.post(
    "",
    response_model=MintMcpTokenResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_same_origin)],
)
def mint_my_token(
    body: MintMcpTokenRequest,
    request: Request,
) -> MintMcpTokenResponse:
    """Mint a fresh ``lmcp_…`` token for the caller. Returns the plaintext ONCE.

    ``MintMcpTokenRequest.model_config = ConfigDict(extra='forbid')``
    rejects unknown fields with HTTP 422 (D4 / D16 — ``expires_at`` is
    intentionally not yet supported; rejecting it is part of the
    contract, not an oversight).
    """
    caller = get_user(request)
    internal, plaintext = mcp_tokens_service.mint(
        caller.user_id,
        body.label.strip(),
    )
    mcp_tokens_service._emit_audit(
        mcp_tokens_service.ACTION_MINTED,
        user_id=caller.user_id,
        input_summary={"label": internal.label},
        result_summary={
            "token_id": internal.id,
            "token_prefix": internal.token_prefix,
        },
    )
    _log.info(
        "mcp_tokens: minted token_id=%s for user_id=%s",
        internal.id, caller.user_id,
    )
    return MintMcpTokenResponse(
        token=_to_public(internal),
        plaintext=plaintext,
    )


@router.delete(
    "/{token_id}",
    response_model=McpTokenPublic,
    dependencies=[Depends(require_same_origin)],
)
def revoke_my_token(
    token_id: str,
    request: Request,
) -> McpTokenPublic:
    """Revoke one of the caller's own tokens. Idempotent.

    Returns 404 (NOT 403) when the row exists but belongs to another
    user — see the information-leak guard in this module's docstring.
    Returns 404 when the row does not exist at all. Re-revoking an
    already-revoked row is a no-op that returns the existing
    ``revoked_at``.
    """
    caller = get_user(request)
    existing = mcp_tokens_service.get_by_id(token_id)
    if existing is None or existing.user_id != caller.user_id:
        raise HTTPException(status_code=404, detail="mcp token not found")

    revoked = mcp_tokens_service.revoke(
        token_id,
        by_user_id=caller.user_id,
        by_role=caller.role,
    )
    if revoked is None:
        # Race: the row vanished between get_by_id and revoke. Treat as 404.
        raise HTTPException(status_code=404, detail="mcp token not found")

    mcp_tokens_service._emit_audit(
        mcp_tokens_service.ACTION_REVOKED,
        user_id=caller.user_id,
        input_summary={"token_id": token_id},
        result_summary={"revoked_at": revoked.revoked_at},
    )
    _log.info(
        "mcp_tokens: revoked token_id=%s by user_id=%s",
        token_id, caller.user_id,
    )
    return _to_public(revoked)


# ---------------------------------------------------------------------------
# Admin routes — /api/v1/admin/users/{user_id}/mcp-tokens
# ---------------------------------------------------------------------------


@admin_router.get(
    "",
    response_model=list[McpTokenAdminView],
)
def admin_list_user_tokens(
    user_id: str,
    include_revoked: bool = True,
) -> list[McpTokenAdminView]:
    """Admin-only enumeration of one user's MCP tokens.

    ``include_revoked`` defaults to ``True`` here (vs ``False`` on the
    user-facing endpoint) because the admin view exists for forensic /
    cleanup purposes — hiding revoked rows by default would be the
    wrong default for that use case. Returns ``404`` when the target
    user does not exist.
    """
    if users_service.get_user_by_id(user_id) is None:
        raise HTTPException(status_code=404, detail="user not found")
    rows = mcp_tokens_service.list_for_user(
        user_id,
        include_revoked=include_revoked,
    )
    return [_to_admin_view(r) for r in rows]


@admin_router.delete(
    "/{token_id}",
    response_model=McpTokenAdminView,
    dependencies=[Depends(require_same_origin)],
)
def admin_revoke_user_token(
    user_id: str,
    token_id: str,
    request: Request,
) -> McpTokenAdminView:
    """Admin revokes a specific user's token. Idempotent.

    Returns ``404`` when:
      * the user does not exist, OR
      * the token does not exist, OR
      * the token's ``user_id`` does not match the path ``user_id``.

    The third case prevents an admin from accidentally revoking a token
    via a mismatched URL and getting an unexpected success — the path's
    ``user_id`` is part of the resource identity, not just routing
    convenience.
    """
    if users_service.get_user_by_id(user_id) is None:
        raise HTTPException(status_code=404, detail="user not found")

    existing = mcp_tokens_service.get_by_id(token_id)
    if existing is None or existing.user_id != user_id:
        raise HTTPException(status_code=404, detail="mcp token not found")

    caller = get_user(request)
    revoked = mcp_tokens_service.revoke(
        token_id,
        by_user_id=caller.user_id,
        by_role=caller.role,
    )
    if revoked is None:
        raise HTTPException(status_code=404, detail="mcp token not found")

    mcp_tokens_service._emit_audit(
        mcp_tokens_service.ACTION_ADMIN_REVOKED,
        user_id=caller.user_id,
        input_summary={
            "token_id": token_id,
            "owner_user_id": user_id,
        },
        result_summary={"revoked_at": revoked.revoked_at},
    )
    _log.info(
        "mcp_tokens: admin user_id=%s revoked token_id=%s (owner=%s)",
        caller.user_id, token_id, user_id,
    )
    return _to_admin_view(revoked)
