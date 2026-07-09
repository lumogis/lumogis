# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Pydantic models for the household invite surface (LUM-186)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from models.auth import LoginResponse
from pydantic import BaseModel
from pydantic import EmailStr
from pydantic import Field


class InviteMintRequest(BaseModel):
    role: Literal["admin", "user"] = "user"
    allows_shared: bool = True


class InviteAdminRow(BaseModel):
    id: str
    role: str
    allows_shared: bool
    created_by: str
    created_at: datetime
    expires_at: datetime
    used_at: datetime | None
    used_by: str | None
    revoked_at: datetime | None
    token_prefix: str | None = None


class InviteMintResponse(BaseModel):
    invite: InviteAdminRow
    invite_url: str
    token: str


class InvitePeekPublic(BaseModel):
    allows_shared: bool
    expires_at: datetime


class InviteRedeemRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=12, max_length=256)


class InviteOnboardingHint(BaseModel):
    allows_shared: bool


class InviteRedeemResponse(LoginResponse):
    invite_onboarding: InviteOnboardingHint


class InternalInvite(BaseModel):
    id: str
    token_prefix: str = Field(repr=False)
    token_hash: str = Field(repr=False)
    role: str
    allows_shared: bool
    created_by: str
    created_at: datetime
    expires_at: datetime
    used_at: datetime | None = None
    used_by: str | None = None
    revoked_at: datetime | None = None


class DuplicateEmailError(Exception):
    """Raised when redeem targets an email that already has an account."""


class InviteInvalidError(Exception):
    """Raised when an invite token is missing, expired, used, or revoked."""


class EmailPolicyViolationError(Exception):
    """Raised when redeem email fails Lumogis auth validation."""
