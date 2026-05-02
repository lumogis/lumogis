# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Pydantic DTOs for the v1 web-client façade.

Stable wire contract consumed by ``clients/lumogis-web`` (and any other
first-party client). Two configuration profiles:

* Request DTOs use ``extra="forbid"`` so unknown fields surface as 422
  during development — codegen drift is loud, not silent.
* Response DTOs use ``extra="ignore"`` so the server can add new fields
  without breaking older clients.

Auth DTOs are **re-exported** from :mod:`orchestrator.models.auth` so
codegen consumes a single source of truth (see plan
``cross_device_lumogis_web`` §Pydantic models / SELF-REVIEW R3 — earlier
plan rounds defined a parallel `LoginRequest`/`LoginResponse`/`UserDTO`
that drifted from the shipped models).

The audit list reuses the actual DB shape returned by
:func:`orchestrator.actions.audit.get_audit` (a row-per-`audit_log`-row
dict). A dedicated :class:`AuditEntryDTO` Pydantic model is exposed so
the OpenAPI snapshot has a stable schema component named ``AuditEntry``
(per plan §Data contracts → ``AuditListResponse``).
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Annotated
from typing import Any
from typing import List
from typing import Literal
from typing import Optional
from typing import Union

from models.auth import AckOk as AckOk  # noqa: F401 — re-export
from models.auth import AdminUserPasswordResetRequest as AdminUserPasswordResetRequest  # noqa: F401
from models.auth import LoginRequest as LoginRequest  # noqa: F401 — re-export
from models.auth import LoginResponse as LoginResponse  # noqa: F401 — re-export
from models.auth import MePasswordChangeRequest as MePasswordChangeRequest  # noqa: F401
from models.auth import UserPublic as UserDTO  # noqa: F401 — re-export under SPA-friendly alias
from pydantic import AnyHttpUrl
from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import model_validator

# Base configs — see module docstring.
_REQ = ConfigDict(extra="forbid", str_strip_whitespace=True)
_RES = ConfigDict(extra="ignore")


# ── Chat ─────────────────────────────────────────────────────────────


class ChatMessageDTO(BaseModel):
    model_config = _REQ
    role: Literal["system", "user", "assistant"]
    content: str = Field(max_length=64_000)


class ChatCompletionRequest(BaseModel):
    model_config = _REQ
    model: str = Field(default="claude", max_length=64)
    messages: List[ChatMessageDTO] = Field(min_length=1, max_length=200)
    stream: bool = True


class ChatCompletionResponse(BaseModel):
    """Non-streaming response shape for ``POST /api/v1/chat/completions``."""

    model_config = _RES
    id: str
    model: str
    message: ChatMessageDTO
    finished_at: datetime


class ModelDescriptor(BaseModel):
    model_config = _RES
    id: str
    label: str
    is_local: bool
    enabled: bool
    provider: str  # "anthropic" | "openai" | "ollama" | ...


class ModelsResponse(BaseModel):
    model_config = _RES
    models: List[ModelDescriptor]


# ── Memory / search ──────────────────────────────────────────────────


class MemorySearchHit(BaseModel):
    model_config = _RES
    id: str
    score: float
    title: Optional[str] = None
    snippet: str = Field(max_length=2_000)
    source: Optional[str] = None
    created_at: Optional[datetime] = None
    scope: Literal["personal", "shared", "system"] = "personal"
    owner_user_id: Optional[str] = None


class MemorySearchResponse(BaseModel):
    model_config = _RES
    hits: List[MemorySearchHit]
    degraded: bool = False
    reason: Optional[str] = None  # e.g. "embedder_not_ready", "vector_store_unavailable"


class RecentSession(BaseModel):
    model_config = _RES
    session_id: str
    summary: str
    ended_at: datetime


class RecentSessionsResponse(BaseModel):
    model_config = _RES
    sessions: List[RecentSession]


# ── KG ───────────────────────────────────────────────────────────────


class EntityCard(BaseModel):
    model_config = _RES
    entity_id: str
    name: str
    type: Optional[str] = None
    aliases: List[str] = []
    summary: Optional[str] = None
    sources: List[str] = []
    scope: Literal["personal", "shared", "system"] = "personal"
    owner_user_id: Optional[str] = None


class RelatedEntity(BaseModel):
    model_config = _RES
    entity_id: str
    name: str
    relation: str
    weight: Optional[float] = None


class RelatedEntitiesResponse(BaseModel):
    model_config = _RES
    related: List[RelatedEntity]


class EntitySearchResponse(BaseModel):
    model_config = _RES
    entities: List[EntityCard]


# ── Approvals ────────────────────────────────────────────────────────


class RiskTier(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"
    hard_limit = "hard_limit"


class DeniedActionItem(BaseModel):
    """Surfaced from ``action_log WHERE allowed=false``."""

    model_config = _RES
    kind: Literal["denied_action"] = "denied_action"
    action_log_id: int
    connector: str
    action_type: str
    risk_tier: RiskTier
    input_summary: Optional[str] = None
    occurred_at: datetime
    elevation_eligible: bool
    suggested_action: Literal[
        "set_connector_do",
        "elevate_action_type",
        "explain_only",
    ]


class ElevationCandidateItem(BaseModel):
    """Surfaced from ``routine_do_tracking`` rows that have hit the threshold."""

    model_config = _RES
    kind: Literal["elevation_candidate"] = "elevation_candidate"
    connector: str
    action_type: str
    approval_count: int
    risk_tier: RiskTier
    elevation_eligible: bool


PendingApprovalItem = Annotated[
    Union[DeniedActionItem, ElevationCandidateItem],
    Field(discriminator="kind"),
]


class PendingApprovalsResponse(BaseModel):
    model_config = _RES
    pending: List[PendingApprovalItem]


class ConnectorModeRequest(BaseModel):
    model_config = _REQ
    mode: Literal["ASK", "DO"]


class ConnectorModeResponse(BaseModel):
    model_config = _RES
    connector: str
    mode: Literal["ASK", "DO"]


class ElevateRequest(BaseModel):
    model_config = _REQ
    connector: str = Field(min_length=1, max_length=128)
    action_type: str = Field(min_length=1, max_length=128)


class ElevateResponse(BaseModel):
    model_config = _RES
    connector: str
    action_type: str
    elevated: Literal[True] = True


# ── Audit ────────────────────────────────────────────────────────────


class AuditEntryDTO(BaseModel):
    """Wire shape for one row of ``audit_log``.

    Mirrors the dict returned by
    :func:`orchestrator.actions.audit.get_audit` (see migration 016 +
    shipped column set: ``id, action_name, connector, mode, input_summary,
    result_summary, reverse_token, reverse_action, executed_at,
    reversed_at``). Schema component is named ``AuditEntry`` so the
    OpenAPI snapshot remains stable for codegen — plan
    `cross_device_lumogis_web` §Data contracts → AuditListResponse.
    """

    model_config = ConfigDict(extra="ignore", from_attributes=True)
    # `from_attributes=True` lets handlers build via `AuditEntryDTO.model_validate(row)`
    # whether `row` is a dict (psycopg RealDictRow) or a dataclass instance.
    id: int
    action_name: str
    connector: str
    mode: str
    input_summary: Optional[str] = None
    result_summary: Optional[str] = None
    reverse_token: Optional[str] = None
    reverse_action: Optional[Any] = None
    executed_at: Optional[datetime] = None
    reversed_at: Optional[datetime] = None


# Expose the schema under the bare name "AuditEntry" so the OpenAPI
# component matches the plan's contract wording without dragging the
# dataclass from `models.actions` (which is server-internal and lacks
# the DB-side fields like `id` and `reversed_at`).
AuditEntry = AuditEntryDTO


class AuditListResponse(BaseModel):
    model_config = _RES
    audit: List[AuditEntryDTO]


class AuditReverseResponse(BaseModel):
    model_config = _RES
    status: Literal["reversed"] = "reversed"
    reverse_token: str


# ── Notifications ────────────────────────────────────────────────────


class WebPushKeys(BaseModel):
    model_config = _REQ
    p256dh: str = Field(min_length=1, max_length=256)
    auth: str = Field(min_length=1, max_length=256)


class WebPushSubscriptionInput(BaseModel):
    model_config = _REQ
    endpoint: AnyHttpUrl
    keys: WebPushKeys
    user_agent: Optional[str] = Field(default=None, max_length=256)
    notify_on_signals: Optional[bool] = Field(
        default=None,
        description="Optional; omit to use defaults (false/new insert, unchanged on duplicate).",
    )
    notify_on_shared_scope: Optional[bool] = Field(
        default=None,
        description="Optional; omit to use defaults (true/new insert).",
    )


class WebPushSubscriptionCreated(BaseModel):
    model_config = _RES
    id: int
    already_existed: bool


class VapidPublicKeyResponse(BaseModel):
    model_config = _RES
    public_key: str


class WebPushSubscriptionPrefsPatch(BaseModel):
    """``PATCH …/notifications/subscriptions/{id}`` — at least one field required."""

    model_config = _REQ
    notify_on_signals: Optional[bool] = None
    notify_on_shared_scope: Optional[bool] = None

    @model_validator(mode="after")
    def _at_least_one(self) -> WebPushSubscriptionPrefsPatch:
        if self.notify_on_signals is None and self.notify_on_shared_scope is None:
            raise ValueError("at least one of notify_on_signals, notify_on_shared_scope required")
        return self


class WebPushSubscriptionRedacted(BaseModel):
    """Non-secret web push subscription row (GET / PATCH responses)."""

    model_config = _RES
    id: int
    endpoint_origin: str = Field(description="scheme + authority only — no path or secrets.")
    created_at: datetime
    last_seen_at: datetime
    last_error: Optional[str] = Field(default=None, max_length=520)
    user_agent: Optional[str] = Field(default=None, max_length=256)
    notify_on_signals: bool
    notify_on_shared_scope: bool


class WebPushSubscriptionsListResponse(BaseModel):
    model_config = _RES
    subscriptions: List[WebPushSubscriptionRedacted]


# ── Captures (Phase 5) ───────────────────────────────────────────────

# Shared tag constraint used by both create models.
_TagList = Annotated[
    Optional[List[Annotated[str, Field(min_length=1, max_length=64)]]],
    Field(default=None, description="At most 20 tags; each 1–64 chars."),
]


class CaptureTextRequest(BaseModel):
    """Backward-compatible alias body for ``POST /api/v1/captures/text``.

    The canonical create route (``POST /api/v1/captures``) accepts
    ``CaptureCreateRequest`` which also allows ``url`` and ``client_id``.
    Both routes share the same handler and response model (plan §12.4).
    """

    model_config = _REQ
    text: str = Field(min_length=1, max_length=32_000)
    title: Optional[str] = Field(default=None, max_length=256)
    scope: Literal["personal", "shared"] = "personal"
    tags: _TagList = None

    @model_validator(mode="after")
    def _cap_tag_count(self) -> "CaptureTextRequest":
        if self.tags is not None and len(self.tags) > 20:
            raise ValueError("at most 20 tags")
        return self


class CaptureCreateRequest(BaseModel):
    """Canonical body for ``POST /api/v1/captures``.

    ``text`` and ``url`` are both optional individually; at least one of
    them must be provided (or a subsequent ``POST …/attachments`` call
    must supply the media). ``client_id`` is the wire name for the
    client-generated UUID stored as ``local_client_id`` in the DB —
    used for idempotent replay (plan §7, MVP freeze 2026-04-29).
    """

    model_config = _REQ
    text: Optional[str] = Field(default=None, max_length=32_000)
    title: Optional[str] = Field(default=None, max_length=256)
    url: Optional[str] = Field(default=None, max_length=2048)
    client_id: Optional[str] = Field(
        default=None,
        description="Client-generated UUID for idempotent replay (local_capture_id).",
    )
    tags: _TagList = None

    @model_validator(mode="after")
    def _require_content(self) -> "CaptureCreateRequest":
        if self.text is None and self.url is None:
            raise ValueError("at least one of 'text' or 'url' is required")
        if self.tags is not None and len(self.tags) > 20:
            raise ValueError("at most 20 tags")
        return self


class CapturePatchRequest(BaseModel):
    """Body for ``PATCH /api/v1/captures/{id}`` — pending captures only."""

    model_config = _REQ
    text: Optional[str] = Field(default=None, max_length=32_000)
    title: Optional[str] = Field(default=None, max_length=256)
    url: Optional[str] = Field(default=None, max_length=2048)
    tags: _TagList = None


class CaptureCreated(BaseModel):
    """Success response for capture create (``201 Created``)."""

    model_config = _RES
    capture_id: str
    status: Literal["pending"]


class CaptureAttachmentSummary(BaseModel):
    """Attachment metadata row — used in both list and detail responses."""

    model_config = _RES
    id: str
    attachment_type: Literal["image", "audio"]
    mime_type: str
    size_bytes: int
    original_filename: Optional[str] = None
    processing_status: Literal["stored", "failed"]
    created_at: datetime


class CaptureTranscriptSummary(BaseModel):
    """Transcript metadata row — attached to the detail view of a capture."""

    model_config = _RES
    id: str
    attachment_id: str
    transcript_status: Literal["pending", "processing", "complete", "failed", "unavailable"]
    transcript_text: Optional[str] = None
    transcript_provenance: Literal["server_stt", "mobile_local_stt", "mobile_direct_provider_stt"]
    language: Optional[str] = None
    confidence: Optional[float] = None
    created_at: datetime
    updated_at: datetime


class CaptureDetail(BaseModel):
    """Full capture row with nested attachment and transcript summaries.

    Returned by ``GET /api/v1/captures/{id}``, ``PATCH …``, and
    ``POST …/index`` (plan §12.4).
    """

    model_config = _RES
    id: str
    status: Literal["pending", "failed", "indexed"]
    capture_type: Literal["text", "url", "photo", "voice", "mixed"]
    title: Optional[str] = None
    text: Optional[str] = None
    url: Optional[str] = None
    tags: Optional[List[str]] = None
    note_id: Optional[str] = None
    source_channel: str
    last_error: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    captured_at: Optional[datetime] = None
    indexed_at: Optional[datetime] = None
    attachments: List[CaptureAttachmentSummary] = []
    transcripts: List[CaptureTranscriptSummary] = []


class CaptureListItem(BaseModel):
    """Summary row for ``GET /api/v1/captures`` list (no nested children)."""

    model_config = _RES
    id: str
    status: Literal["pending", "failed", "indexed"]
    capture_type: Literal["text", "url", "photo", "voice", "mixed"]
    title: Optional[str] = None
    text: Optional[str] = None
    url: Optional[str] = None
    attachment_count: int = 0
    transcript_count: int = 0
    created_at: datetime
    updated_at: datetime


class CaptureListResponse(BaseModel):
    """Paginated list response for ``GET /api/v1/captures``."""

    model_config = _RES
    captures: List[CaptureListItem]
    total: int
    limit: int
    offset: int


class CaptureTranscribeRequest(BaseModel):
    """Body for ``POST /api/v1/captures/{id}/transcribe``.

    ``attachment_id`` targets a specific audio attachment; omit to
    transcribe all pending audio attachments on the capture (plan §12.4).
    """

    model_config = _REQ
    attachment_id: Optional[str] = Field(
        default=None,
        description="UUID of the audio attachment to transcribe. Omit for all pending audio.",
    )


# ── Me / tools catalog (read-only façade) ───────────────────────────


class MeToolsSummary(BaseModel):
    """Aggregate counts for ``GET /api/v1/me/tools``."""

    model_config = _RES
    total: int = Field(ge=0)
    available: int = Field(ge=0)
    unavailable: int = Field(ge=0)
    by_source: dict[str, int] = Field(
        description="Count of catalog rows per ``source`` (core, plugin, mcp, …).",
    )


class MeToolsItem(BaseModel):
    """One user-safe tool row — observational only; not an execution contract."""

    model_config = _RES
    name: str
    label: str = Field(description="Short UI label derived from description or tool name.")
    description: str = Field(
        default="",
        description="Plain-text summary only; never a JSON Schema blob.",
    )
    source: str
    transport: str
    origin_tier: str
    available: bool
    why_not_available: Optional[str] = None
    capability_id: Optional[str] = None
    connector: Optional[str] = None
    action_type: Optional[str] = None
    permission_mode: str
    requires_credentials: bool = False


class MeToolsResponse(BaseModel):
    """Response for ``GET /api/v1/me/tools`` — unified read-only tool catalog view."""

    model_config = _RES
    tools: List[MeToolsItem]
    summary: MeToolsSummary


# ── Me / LLM providers (read-only façade) ───────────────────────────


class MeLlmProviderItem(BaseModel):
    """One cloud LLM vendor row — observational metadata only."""

    model_config = _RES
    connector: str = Field(description="Registered connector id (e.g. ``llm_openai``).")
    label: str = Field(description="Short UI label (e.g. ``OpenAI``).")
    description: str = Field(
        default="",
        description="Registry-derived blurb without payload/key material.",
    )
    configured: bool = Field(
        description="True if a tier or env fallback may supply this connector.",
    )
    active_tier: Literal["user", "household", "system", "env", "none"] = Field(
        description="First tier with a stored row, else env if fallback applies, else none.",
    )
    user_credential_present: bool = False
    household_credential_available: bool = False
    system_credential_available: bool = False
    env_fallback_available: bool = False
    updated_at: Optional[datetime] = Field(
        default=None,
        description="Last update time for the winning metadata row, if any.",
    )
    key_version: Optional[int] = Field(
        default=None,
        description="Encryption key version for the winning metadata row, if any.",
    )
    status: Literal["configured", "not_configured"]
    why_not_available: Optional[str] = Field(
        default=None,
        description="Human hint when not configured; never contains secrets.",
    )


class MeLlmProvidersSummary(BaseModel):
    """Aggregate counts for ``GET /api/v1/me/llm-providers``."""

    model_config = _RES
    total: int = Field(ge=0)
    configured: int = Field(ge=0)
    not_configured: int = Field(ge=0)
    by_active_tier: dict[str, int] = Field(
        description="Counts per ``active_tier`` value (``user``, ``household``, …).",
    )


class MeLlmProvidersResponse(BaseModel):
    """Response for ``GET /api/v1/me/llm-providers`` — curated LLM credential status."""

    model_config = _RES
    providers: List[MeLlmProviderItem]
    summary: MeLlmProvidersSummary


# ── Me / notifications (read-only façade) ───────────────────────────


class MeNotificationChannelItem(BaseModel):
    """One notification channel — observational metadata only."""

    model_config = _RES
    connector: str = Field(
        description="Registry connector id (e.g. ``ntfy``) or façade id ``web_push``.",
    )
    label: str
    description: str = Field(default="", description="Safe UI copy; no secrets.")
    configured: bool = Field(
        description="True when the channel can be considered active for this user.",
    )
    active_tier: Literal["user", "household", "system", "env", "none"] = Field(
        description="Credential tier for ntfy; ``user``/``none`` for web push rows.",
    )
    user_credential_present: bool = False
    household_credential_available: bool = False
    system_credential_available: bool = False
    env_fallback_available: bool = False
    url: Optional[str] = Field(
        default=None,
        description="Deployment ntfy base URL when derived from env fallback only.",
    )
    url_configured: Optional[bool] = Field(
        default=None,
        description="Unknown (null) when credentials are encrypted at rest.",
    )
    topic_configured: Optional[bool] = Field(
        default=None,
        description="Unknown (null) when credentials are encrypted at rest.",
    )
    token_configured: Optional[bool] = Field(
        default=None,
        description="Whether an auth token is set — boolean only, never the value.",
    )
    updated_at: Optional[datetime] = None
    key_version: Optional[int] = None
    subscription_count: Optional[int] = Field(
        default=None,
        description="Web Push: number of browser subscriptions for this user.",
    )
    push_service_configured: Optional[bool] = Field(
        default=None,
        description="Web Push: whether VAPID keys are set on the server.",
    )
    status: Literal["configured", "not_configured"]
    why_not_available: Optional[str] = Field(
        default=None,
        description="Hint when not configured; never contains secrets.",
    )


class MeNotificationsSummary(BaseModel):
    """Aggregate counts for ``GET /api/v1/me/notifications``."""

    model_config = _RES
    total: int = Field(ge=0)
    configured: int = Field(ge=0)
    not_configured: int = Field(ge=0)
    by_active_tier: dict[str, int] = Field(
        description="Counts per ``active_tier`` value.",
    )


class MeNotificationsResponse(BaseModel):
    """Response for ``GET /api/v1/me/notifications`` — curated notification status."""

    model_config = _RES
    channels: List[MeNotificationChannelItem]
    summary: MeNotificationsSummary


# ── Admin / diagnostics (read-only façade) ───────────────────────────


class AdminDiagnosticsCore(BaseModel):
    """Core instance flags for ``GET /api/v1/admin/diagnostics``."""

    model_config = _RES
    auth_enabled: bool = Field(description="Whether JWT auth is enforced (``AUTH_ENABLED``).")
    tool_catalog_enabled: bool = Field(
        description=(
            "Whether unified tool catalog merge for LLM is on (``LUMOGIS_TOOL_CATALOG_ENABLED``)."
        ),
    )
    core_version: str = Field(description="Running Core semver string.")
    mcp_enabled: bool = Field(description="Whether the MCP server package initialised.")
    mcp_auth_required: bool = Field(
        description="Whether ``MCP_AUTH_TOKEN`` is set (clients must send a bearer token).",
    )


class AdminDiagnosticsStoreItem(BaseModel):
    """Backend store reachability (ping-only; no connection strings)."""

    model_config = _RES
    name: str
    status: Literal["ok", "unreachable", "unknown", "not_configured"] = Field(
        description="``not_configured`` when optional backend (e.g. graph) is disabled.",
    )
    message: Optional[str] = Field(
        default=None,
        description="Short operator hint only; never secrets or stack traces.",
    )


class AdminDiagnosticsCapabilityService(BaseModel):
    """One registered out-of-process capability service (manifest metadata only)."""

    model_config = _RES
    id: str = Field(description="Manifest ``id``.")
    status: Literal["healthy", "unhealthy"] = Field(
        description="Derived from last registry health probe state.",
    )
    healthy: bool
    version: str
    last_seen: Optional[datetime] = Field(
        default=None,
        description="UTC timestamp of last successful health probe.",
    )
    tools: int = Field(ge=0, description="Tool count from manifest.")


class AdminDiagnosticsCapabilities(BaseModel):
    """Capability registry summary."""

    model_config = _RES
    total: int = Field(ge=0)
    healthy: int = Field(ge=0)
    unhealthy: int = Field(ge=0)
    services: List[AdminDiagnosticsCapabilityService] = Field(
        description="Sorted by ``id`` for stable JSON.",
    )


class AdminDiagnosticsTools(BaseModel):
    """Unified tool catalog counts (same semantics as ``GET /api/v1/me/tools`` summary)."""

    model_config = _RES
    total: int = Field(ge=0)
    available: int = Field(ge=0)
    unavailable: int = Field(ge=0)
    by_source: dict[str, int] = Field(description="Counts keyed by ``ToolCatalogEntry.source``.")


class AdminDiagnosticsWarning(BaseModel):
    """Safe operator warning (no raw exceptions)."""

    model_config = _RES
    code: str
    message: str


class AdminDiagnosticsSpeechToText(BaseModel):
    """Speech-to-text readiness slice (foundation STT — see ``speech_to_text`` ADR chunk)."""

    model_config = _RES
    backend: Literal["none", "fake_stt", "whisper_sidecar"]
    transcribe_available: bool
    max_audio_bytes: int
    max_duration_sec: int
    endpoint: str = Field(default="/api/v1/voice/transcribe")


# ── Voice / transcription (public HTTP + port result types) ──────────


class TranscriptionSegment(BaseModel):
    """One timed segment returned by adapters that provide word timestamps."""

    model_config = _REQ
    start: float = Field(ge=0.0)
    end: float = Field(ge=0.0)
    text: str


class TranscriptionResult(BaseModel):
    """Portable result returned by :func:`ports.speech_to_text.SpeechToText.transcribe`."""

    model_config = _REQ
    text: str
    language: Optional[str] = None
    duration_seconds: Optional[float] = None
    provider: str
    model: Optional[str] = None
    segments: List[TranscriptionSegment] = Field(default_factory=list)


class VoiceTranscribeResponse(BaseModel):
    """Wire shape for ``POST /api/v1/voice/transcribe``."""

    model_config = _RES
    text: str
    language: Optional[str] = None
    duration_seconds: Optional[float] = None
    provider: str
    model: Optional[str] = None
    segments: List[TranscriptionSegment] = Field(default_factory=list)


class AdminDiagnosticsResponse(BaseModel):
    """Response for ``GET /api/v1/admin/diagnostics`` — curated operator diagnostics."""

    model_config = _RES
    status: Literal["ok", "degraded"] = Field(
        description="``degraded`` when Postgres or another required store is not ``ok``.",
    )
    generated_at: datetime = Field(description="UTC timestamp when the snapshot was built.")
    core: AdminDiagnosticsCore
    stores: List[AdminDiagnosticsStoreItem]
    capabilities: AdminDiagnosticsCapabilities
    tools: AdminDiagnosticsTools
    warnings: List[AdminDiagnosticsWarning]
    speech_to_text: AdminDiagnosticsSpeechToText


# ── Errors ───────────────────────────────────────────────────────────


class ErrorResponse(BaseModel):
    model_config = _RES
    error: str  # stable machine code
    detail: Optional[str] = None


__all__ = [
    "LoginRequest",
    "LoginResponse",
    "UserDTO",
    "ChatMessageDTO",
    "ChatCompletionRequest",
    "ChatCompletionResponse",
    "ModelDescriptor",
    "ModelsResponse",
    "MemorySearchHit",
    "MemorySearchResponse",
    "RecentSession",
    "RecentSessionsResponse",
    "EntityCard",
    "RelatedEntity",
    "RelatedEntitiesResponse",
    "EntitySearchResponse",
    "RiskTier",
    "DeniedActionItem",
    "ElevationCandidateItem",
    "PendingApprovalItem",
    "PendingApprovalsResponse",
    "ConnectorModeRequest",
    "ConnectorModeResponse",
    "ElevateRequest",
    "ElevateResponse",
    "AuditEntry",
    "AuditEntryDTO",
    "AuditListResponse",
    "AuditReverseResponse",
    "WebPushKeys",
    "WebPushSubscriptionInput",
    "WebPushSubscriptionCreated",
    "WebPushSubscriptionPrefsPatch",
    "WebPushSubscriptionRedacted",
    "WebPushSubscriptionsListResponse",
    "VapidPublicKeyResponse",
    "CaptureTextRequest",
    "CaptureCreated",
    "MeToolsItem",
    "MeToolsSummary",
    "MeToolsResponse",
    "MeLlmProviderItem",
    "MeLlmProvidersSummary",
    "MeLlmProvidersResponse",
    "MeNotificationChannelItem",
    "MeNotificationsSummary",
    "MeNotificationsResponse",
    "AdminDiagnosticsCore",
    "AdminDiagnosticsStoreItem",
    "AdminDiagnosticsCapabilityService",
    "AdminDiagnosticsCapabilities",
    "AdminDiagnosticsTools",
    "AdminDiagnosticsWarning",
    "AdminDiagnosticsSpeechToText",
    "TranscriptionSegment",
    "TranscriptionResult",
    "VoiceTranscribeResponse",
    "AdminDiagnosticsResponse",
    "ErrorResponse",
]
