# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""``GET /api/v1/admin/feature-flags`` — admin visibility into experimental gates.

LUM-126. Read-only window onto :mod:`features`: lets an admin see which
disabled-by-default experimental flags exist and which are currently enabled,
without grepping env files. Returns flag metadata + boolean state only — never
secrets or env values beyond the flag's own truthiness.
"""

from __future__ import annotations

import features
from authz import require_admin
from fastapi import APIRouter
from fastapi import Depends
from models.api_v1 import FeatureFlagsResponse
from models.api_v1 import FeatureFlagState

router = APIRouter(
    prefix="/api/v1/admin/feature-flags",
    tags=["v1-feature-flags"],
    dependencies=[Depends(require_admin)],
)


@router.get("", response_model=FeatureFlagsResponse)
def get_feature_flags() -> FeatureFlagsResponse:
    flags = [FeatureFlagState(**row) for row in features.snapshot()]
    return FeatureFlagsResponse(
        total=len(flags),
        enabled=sum(1 for f in flags if f.enabled),
        flags=flags,
    )
