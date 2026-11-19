# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Request and response models for first-admin setup (LUM-981)."""

from pydantic import BaseModel
from pydantic import EmailStr
from pydantic import Field


class SetupStatus(BaseModel):
    needs_setup: bool


class SetupClaimRequest(BaseModel):
    email: EmailStr
    # No min_length: the length policy comes from ``validate_password_policy``
    # (a string 422 detail, like the other auth routes), not from Pydantic.
    password: str = Field(max_length=256)
