# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Pydantic models for the family-LAN auth surface.

Three layers of user representation, deliberately separate so handlers
can never serialise a hash by accident:

  - ``InternalUser``    : full record, used inside ``services/users.py`` only.
  - ``UserAdminView``   : admin-only listing shape (adds operational fields).
  - ``UserPublic``      : safe response shape (id, email, role).

Request / response shapes for ``/api/v1/auth/*`` and ``/api/v1/admin/users``
also live here so the route module stays thin.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, EmailStr, Field


class AckOk(BaseModel):
    """Generic success ack for mutating endpoints that return no resource body."""

    ok: Literal[True] = True

Role = Literal["admin", "user"]


class UserPublic(BaseModel):
    """Safe response shape — never includes hash, disabled, or last_login_at.

    Returned by ``GET /api/v1/auth/me`` and embedded in ``LoginResponse.user``.
    """

    id: str
    email: EmailStr
    role: Role


class UserAdminView(UserPublic):
    """Admin-only listing shape returned by ``/api/v1/admin/users`` routes.

    Adds operational fields useful to administrators (account state, age,
    last sign-in). Still excludes ``password_hash`` and ``refresh_token_jti``
    — those are server-internal.
    """

    disabled: bool
    created_at: datetime
    last_login_at: datetime | None = None


class InternalUser(BaseModel):
    """Server-side full user record. Never serialised to a client.

    Used as the return value of internal helpers in ``services/users.py``.
    Routes must convert to ``UserPublic`` or ``UserAdminView`` before
    returning a response.
    """

    id: str
    email: EmailStr
    password_hash: str
    role: Role
    disabled: bool = False
    created_at: datetime
    last_login_at: datetime | None = None
    refresh_token_jti: str | None = None


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=12, max_length=256)


class LoginResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int  # seconds — equals ACCESS_TOKEN_TTL_SECONDS
    user: UserPublic


class UserCreateRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=12, max_length=256)
    role: Role = "user"


class UserPatchRequest(BaseModel):
    role: Role | None = None
    disabled: bool | None = None


class MePasswordChangeRequest(BaseModel):
    """Body for ``POST /api/v1/me/password`` — policy enforced again in :mod:`services.users`."""

    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=1, max_length=256)


class AdminUserPasswordResetRequest(BaseModel):
    """Body for ``POST /api/v1/admin/users/{user_id}/password``."""

    new_password: str = Field(min_length=1, max_length=256)
