# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""User notification preferences + admin tier policy routes."""

from __future__ import annotations

from auth import UserContext
from authz import require_admin
from authz import require_user
from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException
from models.notifications import NotificationPreferencesPatch
from models.notifications import NotificationPreferencesResponse
from models.notifications import NotificationTier
from models.notifications import NotificationTierPolicyPatch
from models.notifications import NotificationTierPolicyRow
from services.notifications import preferences as prefs_svc

me_router = APIRouter(
    prefix="/api/v1/me/notification-preferences",
    tags=["v1-notification-preferences"],
    dependencies=[Depends(require_user)],
)

admin_router = APIRouter(
    prefix="/api/v1/admin/notification-tier-policy",
    tags=["v1-notification-tier-policy"],
    dependencies=[Depends(require_admin)],
)


@me_router.get("", response_model=NotificationPreferencesResponse)
def get_my_notification_preferences(user: UserContext = Depends(require_user)):
    return prefs_svc.get_effective_preferences(user.user_id)


@me_router.patch("", response_model=NotificationPreferencesResponse)
def patch_my_notification_preferences(
    body: NotificationPreferencesPatch,
    user: UserContext = Depends(require_user),
):
    try:
        return prefs_svc.patch_preferences(user.user_id, body)
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="preference_update_failed") from None


@admin_router.get("", response_model=list[NotificationTierPolicyRow])
def get_notification_tier_policies():
    return prefs_svc.get_tier_policies()


@admin_router.patch("/{tier}", response_model=NotificationTierPolicyRow)
def patch_notification_tier_policy(
    tier: NotificationTier,
    body: NotificationTierPolicyPatch,
    user: UserContext = Depends(require_admin),
):
    try:
        return prefs_svc.patch_tier_policy(tier, body, actor_user_id=user.user_id)
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="tier_policy_update_failed") from None
