# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Pydantic models for the per-user connector credentials HTTP surface.

Aligns with :class:`services.connector_credentials.CredentialRecord` metadata
columns **except** :attr:`~services.connector_credentials.CredentialRecord.delivery_paused_detail`,
which is DB/diagnostic-only and intentionally omitted from the wire model.
Routes must project with an explicit dict (or ``dataclasses.asdict`` minus
that key) — never ``ConnectorCredentialPublic.model_validate(record.__dict__)``
verbatim. Plaintext payload and ciphertext bytes NEVER appear on this surface.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from typing import ClassVar
from typing import Literal

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import field_validator


class ConnectorCredentialPublic(BaseModel):
    """Safe-for-wire projection. Never ciphertext, never plaintext."""

    model_config = ConfigDict(extra="forbid")

    user_id: str
    connector: str
    # LUM-170: instance discriminator for multi-instance connectors (e.g. imap
    # keys per-mailbox by source_id). Empty string for single-instance connectors.
    # Required here because _to_public projects via asdict(CredentialRecord),
    # which now carries `instance`; without this field extra="forbid" would 500
    # every connector's credential route.
    instance: str = ""
    created_at: datetime
    updated_at: datetime
    created_by: str
    updated_by: str
    key_version: int
    delivery_paused: bool = False
    delivery_paused_reason: str | None = None
    delivery_paused_at: datetime | None = None
    # LUM-834 chunk B — user-set on/off, distinct from delivery_paused.
    enabled: bool = True
    #: Payload key NAMES the user has set — never a value.
    #:
    #: ``None`` is a THIRD state and it is load-bearing: the row predates
    #: migration 079, so which fields are set is genuinely unknown. It is not
    #: "no fields set" — a pre-migration credential is usually a working one.
    #:
    #: The card must destructure this explicitly. ``?? []`` / ``|| []`` are
    #: FORBIDDEN on this field in the web client: that idiom appears 60+ times
    #: in `clients/lumogis-web/src` and applying it here collapses unknown into
    #: nothing-set at the render layer, which is the failure the nullable
    #: column exists to prevent.
    configured_fields: list[str] | None = None
    #: Which of the connector's declared fields hold secrets. From the
    #: REGISTRY, never from the row — secrecy is a property of the connector,
    #: not of one user's payload. Never ``None``: the registry always knows.
    secret_fields: list[str] = Field(default_factory=list)


class PatchConnectorCredentialRequest(BaseModel):
    """Request body for ``PATCH /…/connector-credentials/{connector}``.

    Deliberately **not** ``PUT``: on this surface ``PUT`` means whole-payload
    replacement, and reusing it for a flag would be the third time that verb's
    semantics bit this feature.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool


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


class ConnectorTestResponse(BaseModel):
    """``POST /api/v1/me/connector-credentials/{connector}/test`` (LUM-834).

    ``ok`` is **tri-state on purpose**: ``true`` verified, ``false`` tried and
    failed, ``null`` **not attempted**. Collapsing ``null`` into ``false`` would
    render a red ✗ on a connector that has no probe and is perfectly healthy.
    Chunk B's four-state chip is built on the three states; a client that treats
    this as a boolean breaks that card before it is written.

    ``code`` is a ``Literal``, not a ``str``, because this shape freezes into
    ``openapi.snapshot.json`` on first ship and a bare ``str`` widens to
    anything later without a contract break. Adding a code is then a visible
    wire change, which is what it is.

    ``detail`` is a fixed string selected by ``code`` in
    :mod:`services.connector_probes` — **never** an exception message. See that
    module's header for why truncating one is not sanitising it.
    """

    model_config = ConfigDict(extra="forbid")

    ok: bool | None
    code: Literal[
        "verified",
        "auth_failed",
        "unreachable",
        "not_configured",
        "credential_unavailable",
        "invalid_config",
        "unsupported",
        "tested_elsewhere",
        # LUM-834 chunk B. Ninth member, added deliberately — ADR-218 §3 froze
        # this Literal so that widening it is a reviewable wire change rather
        # than a silent one. Carries ok=null (not attempted).
        "disabled",
    ]
    detail: str
