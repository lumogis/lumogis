# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Per-user privacy mode preferences (LUM-194)."""

from __future__ import annotations

from auth import UserContext
from authz import require_user
from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException
from models.privacy_mode import MePrivacyModePatch
from models.privacy_mode import MePrivacyModeResponse

from services import privacy_mode as privacy_svc

router = APIRouter(
    prefix="/api/v1/me/privacy-mode",
    tags=["v1-privacy-mode"],
    dependencies=[Depends(require_user)],
)


@router.get("", response_model=MePrivacyModeResponse)
def get_my_privacy_mode(user: UserContext = Depends(require_user)):
    return MePrivacyModeResponse(**privacy_svc.get_me_privacy_mode(user.user_id))


@router.patch("", response_model=MePrivacyModeResponse)
def patch_my_privacy_mode(
    body: MePrivacyModePatch,
    user: UserContext = Depends(require_user),
):
    try:
        data = privacy_svc.patch_me_privacy_mode(user.user_id, body.user_restriction)
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="privacy_mode_update_failed") from None
    return MePrivacyModeResponse(**data)
