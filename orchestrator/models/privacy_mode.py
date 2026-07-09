# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Pydantic models for cloud LLM privacy mode (LUM-194)."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel


class InstancePrivacyMode(str, Enum):
    LOCAL_ONLY = "local_only"
    ALLOW_CLOUD = "allow_cloud"


class PrivacyUserRestriction(str, Enum):
    INHERIT = "inherit"
    LOCAL_ONLY = "local_only"


class InstancePrivacySettings(BaseModel):
    privacy_mode: InstancePrivacyMode
    privacy_mode_locked: bool
    privacy_effective: InstancePrivacyMode


class InstancePrivacyPatch(BaseModel):
    privacy_mode: InstancePrivacyMode | None = None
    privacy_mode_locked: bool | None = None


class MePrivacyModeResponse(BaseModel):
    instance: InstancePrivacySettings
    user_restriction: PrivacyUserRestriction
    privacy_effective: InstancePrivacyMode
    can_allow_cloud: bool


class MePrivacyModePatch(BaseModel):
    user_restriction: PrivacyUserRestriction
