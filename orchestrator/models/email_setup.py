# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Wire models for the guided mailbox setup flow (LUM-644 Email Setup v2).

- ``ProviderInfoModel`` mirrors ``services.email_providers.ProviderInfo`` for the
  ``GET /api/v1/me/email-providers`` response.
- ``EmailProbeRequest``/``EmailProbeResponse`` are the pre-save live-test contract
  (``POST /me/email-sources/test``). ``auth_type`` is a ``Literal`` so ``xoauth2``
  is a Pydantic 422 rather than a hand-mapped ``ValueError``. Credentials in the
  request are request-scoped only — never persisted, never logged.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Literal

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import field_validator
from pydantic import model_validator

# Canonical mailbox-label shape. Lives here (models have no route deps) so both
# ``models`` and ``routes.signals`` can import it without a circular import
# (routes.signals imports this module at load time) — LUM-687.
_MAILBOX_LABEL_RE = re.compile(r"^[a-z0-9_-]{1,64}$")
# A deliberately-basic addr-spec check (mirrors actions.handlers.smtp_send).
_ADDR_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class ProviderInfoModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    display_name: str
    imap_host: str
    imap_port: int
    smtp_host: str
    smtp_port: int
    app_password_url: str
    help_steps: list[str]
    auth_status: str
    enabled: bool
    disabled_reason: str | None
    notes: str
    label_visible: bool = True  # LUM-645: does the provider display Lumogis labels?
    supports_oauth: bool = False  # LUM-687: OAuth (device-flow) connect available?
    oauth_provider: str | None = None  # the email_oauth provider key (e.g. "microsoft")


class ProvidersResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    providers: list[ProviderInfoModel]
    # LUM-758: will a mailbox added right now actually start syncing, or does this
    # deployment still need an operator? Two gates can hold it back — the in-process
    # egress ceiling (bakes its allowlist at boot; LUM-636) and a proxy whose
    # allow-file watcher has been switched off. Setup must say so BEFORE the member
    # finishes, rather than letting them discover it from a mailbox that never syncs.
    new_mailbox_needs_operator: bool = False
    operator_action_reason: str | None = None


class EmailProbeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Field constraints make structurally-malformed input a Pydantic 422; the route
    # then maps every *connection* failure (SSRF/TLS/DNS/creds) to {ok:false}.
    host: str = Field(min_length=1)
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)
    port: int = Field(default=993, ge=1, le=65535)
    use_ssl: bool = True
    auth_type: Literal["app_password"] = "app_password"


class ProbeFolder(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    role: str | None = None
    hidden: bool = False
    selected: bool = True


class EmailProbeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: bool
    message_count: int | None = None
    folders: list[ProbeFolder] = []
    capabilities: list[str] = []
    error: str | None = None
    # True on a proxy-configured deployment where the pre-save test is not
    # attempted (Layer-1 feature) — the web soft-Saves and verifies on first poll.
    proxy_unverified: bool = False


class EmailSmtpUpdate(BaseModel):
    """Opt-in / opt-out of "let Lumogis send as me" for one mailbox (LUM-650).

    ``enabled=false`` removes the ``smtp`` credential row → ``email_send`` refuses
    for that mailbox (independent send revocation; the IMAP receive credential is
    untouched). On enable, ``reuse_imap_password`` (default true) reuses the
    mailbox's IMAP app-password; otherwise ``app_password`` is required. Host/port
    come from the provider registry for a known provider; an "Other" mailbox must
    supply ``smtp_host``/``smtp_port`` (the route 422s if absent). There is no
    ``from`` — From is always the mailbox's authenticated account.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool
    reuse_imap_password: bool = True
    app_password: str | None = Field(default=None, min_length=1)
    smtp_host: str | None = Field(default=None, min_length=1)
    smtp_port: int | None = Field(default=None, ge=1, le=65535)


class EmailSmtpResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: bool
    enabled: bool
    tested: bool = False  # whether a live pre-save connect-test was performed
    error: str | None = None


# --- OAuth device-flow (Device Authorization Grant, RFC 8628) — LUM-687 -------


class OAuthDeviceStartRequest(BaseModel):
    """Begin an OAuth connect for a mailbox. The provider is the route path param
    (the registry id, e.g. "outlook"); the body carries the mailbox address (the
    XOAUTH2 username) + the label. Request-scoped — no credential material."""

    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3)
    mailbox_label: str = Field(min_length=1)
    # LUM-691 — import-scope parity with the app-password path (EmailSourceRequest
    # in routes/signals.py). An OAuth mailbox is always metadata-mode (no
    # full_ingest option on this path), so the validator below is that request's
    # custom/non-custom date rules WITHOUT the full_ingest branch.
    import_scope: Literal["just_new", "last_7d", "last_30d", "custom"] = "last_30d"
    import_since: date | None = None
    import_before: date | None = None

    @field_validator("email")
    @classmethod
    def _check_email(cls, v: str) -> str:
        a = (v or "").strip()
        if any(ch in a for ch in "\r\n") or any(ord(ch) < 32 for ch in a):
            raise ValueError("email contains a control character")
        if not _ADDR_RE.match(a):
            raise ValueError("malformed email address")
        return a

    @field_validator("mailbox_label")
    @classmethod
    def _check_label(cls, v: str) -> str:
        if not _MAILBOX_LABEL_RE.match(v or ""):
            raise ValueError("mailbox_label must match [a-z0-9_-]{1,64}")
        return v

    @model_validator(mode="after")
    def _validate_import_scope(self) -> "OAuthDeviceStartRequest":
        if self.import_scope == "custom":
            if self.import_since is None:
                raise ValueError("custom import_scope requires import_since")
            if self.import_before is not None and self.import_before < self.import_since:
                raise ValueError("import_before must be >= import_since")
        elif self.import_since is not None or self.import_before is not None:
            raise ValueError("import_since/import_before are only valid for custom scope")
        return self


class OAuthDeviceStartResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    flow_id: str
    user_code: str
    verification_uri: str
    verification_uri_complete: str | None = None
    interval: int
    expires_in: int


class OAuthDevicePollRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    flow_id: str = Field(min_length=1)


class OAuthDevicePollResponse(BaseModel):
    """Wire status is pending/connected/expired/denied/error (the service's
    internal "authorized" is route-mapped to "connected" after it persists).
    ``verified`` is true when the live IMAP probe succeeded, false on a soft-save
    (egress-proxied deployment — verification defers to first sync)."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["pending", "connected", "expired", "denied", "error"]
    source_id: str | None = None
    verified: bool = False
    error: str | None = None
