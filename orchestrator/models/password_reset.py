# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Wire models for forgot-password reset and system mail (LUM-65)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import EmailStr
from pydantic import Field


class PasswordResetStatusResponse(BaseModel):
    """What the public forgot-password page needs to decide what to render.

    One boolean, deliberately: the page must not learn *why* email reset is
    unavailable (no SMTP? no origin? no HMAC?) — that is operator information,
    and it is available to an admin on the System mail page.
    """

    model_config = ConfigDict(extra="forbid")

    email_reset_enabled: bool


class PasswordResetRequestBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    # Serialized ALTCHA solution from the widget (base64 JSON). Field name
    # matches the widget's own `altcha` form field.
    altcha: str = Field(min_length=1, max_length=8192)


class PasswordResetRedeemBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=1, max_length=1024)


class SystemMailPublic(BaseModel):
    """Admin-facing view of the system mailer. Never carries the password.

    A dedicated model rather than ``InstanceSystemConnectorCredentialPublic``:
    that projection sets ``extra="forbid"`` and carries **zero** payload fields
    by design (it describes any system credential row without revealing its
    shape), so it cannot express host/port/from_addr — the things this page
    exists to show.
    """

    model_config = ConfigDict(extra="forbid")

    configured: bool
    host: str | None = None
    port: int | None = None
    username: str | None = None
    from_addr: str | None = None
    password_set: bool = False
    last_test_ok_at: datetime | None = None
    # Enablement is a conjunction; the page shows an operator which conjunct is
    # missing, which is exactly the diagnosis the public status endpoint withholds.
    email_reset_enabled: bool = False
    public_origin_set: bool = False
    altcha_hmac_set: bool = False


class SystemMailPutBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    host: str = Field(min_length=1, max_length=255)
    port: int = Field(ge=1, le=65535, default=587)
    username: str = Field(default="", max_length=255)
    # Omit to keep the stored password; `null` or `""` is rejected so a UI that
    # round-trips a redacted GET cannot silently blank the credential — clearing
    # it is what DELETE is for.
    password: str | None = Field(default=None, max_length=1024)
    from_addr: EmailStr


class SystemMailTestBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Defaults to the calling admin's own account email: the operator has to be
    # able to open the inbox they are proving.
    to: EmailStr | None = None


class SystemMailTestResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: bool
    last_test_ok_at: datetime
