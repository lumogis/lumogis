# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Admin-only read-only diagnostics endpoints.

This module is the natural home for small admin/operator GETs that
surface non-secret system state. **Hard rule:** every endpoint here is
read-only and fail-closed; any endpoint that returns plaintext or
ciphertext belongs elsewhere (or nowhere).

Surfaces
--------
* ``GET /api/v1/admin/diagnostics`` — curated Core/store/capability/tool
  summary for Lumogis Web admin shell (read-only; no secrets). Implemented
  in :mod:`services.admin_diagnostics`.

Initial surface (plan ``credential_management_ux`` D3 + D4)
-----------------------------------------------------------
* ``GET /api/v1/admin/diagnostics/credential-key-fingerprint`` —
  current household-key fingerprint plus per-``key_version`` row
  counts. Powers the rotation-progress badge in the credential
  management UI's admin mode (D10) and gives operators a one-shot
  answer to "is this rotation done yet?" without parsing logs.

Audit posture
-------------
No ``audit_log`` row is written for these GETs (plan D5), consistent
with the existing read-only admin GETs in this codebase (e.g.
``GET /api/v1/admin/users``, ``GET /api/v1/admin/users/{id}/connector-credentials``).
The endpoint exposes only an ``int`` fingerprint and a count
breakdown — no plaintext, no ciphertext, no key bytes.
"""

from __future__ import annotations

import logging
from typing import Callable

from auth import get_user
from authz import require_admin
from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException
from fastapi import Request
from fastapi import status
from models.api_v1 import AdminDiagnosticsResponse

from services import admin_diagnostics as admin_diagnostics_svc
from services import connector_credentials as ccs
from services import credential_tiers as cts

_log = logging.getLogger(__name__)


router = APIRouter(
    prefix="/api/v1/admin/diagnostics",
    tags=["admin-diagnostics"],
    dependencies=[Depends(require_admin)],
)


# Per-tier counter callables used by the credential-key-fingerprint endpoint.
# Order is the documented response key order: user, household, system.
_CounterFn = Callable[[], dict[int, int]]
_TIER_COUNTERS: tuple[tuple[str, _CounterFn], ...] = (
    ("user", ccs.count_rows_by_key_version),
    ("household", cts.household_count_rows_by_key_version),
    ("system", cts.system_count_rows_by_key_version),
)


@router.get("", response_model=AdminDiagnosticsResponse)
def admin_diagnostics(request: Request) -> AdminDiagnosticsResponse:
    """Curated operator diagnostics (admin-only).

    Aggregates existing ping checks and registry/catalog metadata only.
    Does not invoke tools, change health behaviour, or return secrets.
    """
    ctx = get_user(request)
    return admin_diagnostics_svc.build_admin_diagnostics_response(ctx.user_id)


@router.get("/credential-key-fingerprint")
def credential_key_fingerprint() -> dict:
    """Return the current household-key fingerprint + per-tier row counts.

    Wire shape (BREAKING CHANGE per ADR
    ``credential_scopes_shared_system``)::

        {
            "current_key_version": <int>,
            "rows_by_key_version": {
                "user":      {"<int-as-string>": <count>, ...},
                "household": {"<int-as-string>": <count>, ...},
                "system":    {"<int-as-string>": <count>, ...}
            }
        }

    * All three tier keys (``user``, ``household``, ``system``) are
      **always present**, even when their inner dict is empty (``{}``).
      Clients should branch on inner-dict emptiness, not on key
      presence.
    * Inner keys are JSON strings (object keys); the int is the same
      stable per-key fingerprint stored in each tier table's
      ``key_version`` column.
    * ``current_key_version`` is a SINGLE int (the current primary key
      fingerprint). All three tier tables seal under the same
      ``LUMOGIS_CREDENTIAL_KEY[S]`` family, so "current key version"
      is unambiguous across tiers.
    * Counts include rows whose ``connector`` is no longer in the
      canonical :data:`connectors.registry.CONNECTORS` mapping.

    Failure modes (PINNED by plan §`admin_diagnostics.py` D4.1):

    * No usable ``LUMOGIS_CREDENTIAL_KEY[S]`` ⇒
      ``503 credential_unavailable``.
    * Any per-tier ``count_rows_by_key_version()`` raises (transient
      DB error, missing table mid-rollout on a multi-instance deploy,
      etc.) ⇒ ``503 diagnostic_unavailable`` with ``{"tier": "<failing tier>"}``.
      The endpoint does **not** return a partial response — fail-fast
      is the safer ops posture for the rotation-progress decision the
      operator is making.

    NEVER returns ciphertext, plaintext, or key bytes.
    """
    try:
        current = ccs.get_current_key_version()
    except RuntimeError as exc:
        _log.warning(
            "credential_key_fingerprint: key load failed (%s)",
            exc.__class__.__name__,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "credential_unavailable"},
        ) from exc

    rows_by_key_version: dict[str, dict[str, int]] = {}
    for tier_label, counter in _TIER_COUNTERS:
        try:
            tier_counts = counter()
        except Exception as exc:
            _log.warning(
                "diagnostic.tier_count_failed tier=%s exc_class=%s",
                tier_label,
                exc.__class__.__name__,
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "code": "diagnostic_unavailable",
                    "tier": tier_label,
                },
            ) from exc
        rows_by_key_version[tier_label] = {str(k): int(v) for k, v in tier_counts.items()}

    return {
        "current_key_version": int(current),
        "rows_by_key_version": rows_by_key_version,
    }
