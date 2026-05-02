# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Pydantic models for the per-user connector credentials HTTP surface.

Mirrors the row metadata returned by
:class:`services.connector_credentials.CredentialRecord` 1:1 so the
route layer can do
``ConnectorCredentialPublic.model_validate(record.__dict__)`` without
field renaming. Plaintext payload and ciphertext bytes NEVER appear on
this surface.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from typing import ClassVar

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import field_validator


class ConnectorCredentialPublic(BaseModel):
    """Safe-for-wire projection. Never ciphertext, never plaintext."""

    model_config = ConfigDict(extra="forbid")

    user_id: str
    connector: str
    created_at: datetime
    updated_at: datetime
    created_by: str
    updated_by: str
    key_version: int


class PutConnectorCredentialRequest(BaseModel):
    """Request body for ``PUT /…/connector-credentials/{connector}``.

    The plaintext ``payload`` is JSON-encoded by the service and sealed
    with the household MultiFernet before insertion. ``extra="forbid"``
    rejects unknown top-level fields with HTTP 422.
    """

    model_config = ConfigDict(extra="forbid")

    payload: dict[str, Any] = Field(
        ...,
        description="Plaintext JSON-serialisable payload (encrypted at rest).",
    )

    PAYLOAD_MAX_BYTES: ClassVar[int] = 64 * 1024
    """Cap on serialised plaintext size.

    Prevents an authenticated user from forcing arbitrary-size Fernet
    encryption work and storing megabytes of ciphertext per row. 64 KiB
    covers every legitimate credential blob (longest realistic: an
    OAuth bundle with a refresh token + 4-key map is well under 4 KiB).
    """

    @field_validator("payload")
    @classmethod
    def _payload_must_be_non_empty_dict_under_cap(cls, v: dict) -> dict:
        if not isinstance(v, dict):
            raise ValueError("payload must be a JSON object")
        if not v:
            raise ValueError("payload must not be empty")
        encoded = json.dumps(v, sort_keys=True, ensure_ascii=False).encode("utf-8")
        if len(encoded) > cls.PAYLOAD_MAX_BYTES:
            raise ValueError(
                f"payload exceeds max {cls.PAYLOAD_MAX_BYTES} bytes (got {len(encoded)})"
            )
        return v


class ConnectorCredentialList(BaseModel):
    """Wrapper for ``GET /…/connector-credentials`` (list)."""

    model_config = ConfigDict(extra="forbid")

    items: list[ConnectorCredentialPublic]


# ---------------------------------------------------------------------------
# Household + instance/system credential tiers (ADR
# ``credential_scopes_shared_system``). These rows are NEVER user-owned, so
# the wire shape drops ``user_id``. The ``PutConnectorCredentialRequest``
# request body is reused for both PUT routes — the wire shape is identical
# (single ``payload`` object, same 64 KiB cap, same validators).
# ---------------------------------------------------------------------------


class HouseholdConnectorCredentialPublic(BaseModel):
    """Safe-for-wire projection for household-tier credential rows.

    Mirrors :class:`services.credential_tiers.HouseholdCredentialRecord`
    1:1 so the route layer can do ``model_validate(record.__dict__)``.
    NEVER carries ciphertext or plaintext.
    """

    model_config = ConfigDict(extra="forbid")

    connector: str
    created_at: datetime
    updated_at: datetime
    created_by: str
    updated_by: str
    key_version: int


class InstanceSystemConnectorCredentialPublic(BaseModel):
    """Safe-for-wire projection for instance/system-tier credential rows.

    Mirrors :class:`services.credential_tiers.InstanceSystemCredentialRecord`
    1:1 so the route layer can do ``model_validate(record.__dict__)``.
    NEVER carries ciphertext or plaintext.
    """

    model_config = ConfigDict(extra="forbid")

    connector: str
    created_at: datetime
    updated_at: datetime
    created_by: str
    updated_by: str
    key_version: int


class HouseholdConnectorCredentialList(BaseModel):
    """Wrapper for ``GET /api/v1/admin/connector-credentials/household``."""

    model_config = ConfigDict(extra="forbid")

    items: list[HouseholdConnectorCredentialPublic]


class InstanceSystemConnectorCredentialList(BaseModel):
    """Wrapper for ``GET /api/v1/admin/connector-credentials/system``."""

    model_config = ConfigDict(extra="forbid")

    items: list[InstanceSystemConnectorCredentialPublic]
