# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Canonical per-user connector registry (single source of truth).

Source-of-truth for the ``connector`` column in
``user_connector_credentials``. The DB CHECK constraints
(``^[a-z0-9_]+$``, length 1..64) on
``postgres/migrations/015-user-connector-credentials.sql`` are a safety
net; this module is the authoritative list at the application layer.

Canonical mapping
-----------------
:data:`CONNECTORS` is the **single** declaration: ``id -> ConnectorSpec``.
Adding a new connector is one edit (or one ``register(..., description=...)``
call) — there is no separate description table to remember to update.
This is a deliberate refactor of the previous parallel-structure design
(``REGISTERED_CONNECTORS`` + ``CONNECTOR_DESCRIPTIONS``) where forgetting
the second half was caught only at request/CI time.

Backward-compatible re-exports
------------------------------
:data:`REGISTERED_CONNECTORS` is retained as a derived
``frozenset[str]`` view so existing call sites
(``services.connector_credentials``, route docstrings, the no-raw-user_id
acceptance gate, etc.) keep working without churn. It is rebound by
:func:`register` for the same reason.

Registry-strictness vocabulary used elsewhere in this chunk:

* :func:`validate_format` — pure format check; **does NOT** verify
  membership in :data:`CONNECTORS`. Used by the metadata-read /
  delete service paths so admins can read or clean up rows whose
  connector id has since left the registry (``get_record``,
  ``list_records``, ``delete_payload``).
* :func:`require_registered` — format-check + registration-check.
  Used by the plaintext-bearing / write paths so unknown connectors
  fail closed (``put_payload``, ``get_payload``, ``resolve``).

Mutation note
-------------
:func:`register` rebinds both :data:`CONNECTORS` (in place) and the
derived :data:`REGISTERED_CONNECTORS` (because ``frozenset`` is
immutable). Modules that did
``from connectors.registry import REGISTERED_CONNECTORS`` will hold a
stale reference. Always go through :func:`is_registered` /
:func:`require_registered` instead of caching the set itself.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

CONNECTOR_FORMAT = re.compile(r"^[a-z0-9_]+$")
CONNECTOR_MAX_LEN = 64


TESTCONNECTOR = "testconnector"
"""Synthetic connector for end-to-end plumbing tests.

Not a real external system; used by the ``testconnector`` integration
test and the rotate-key script's smoke check. Safe to leave registered
in production — the credential row, if any, decrypts to a no-op payload
and no consumer route reads it.
"""


NTFY = "ntfy"
"""ntfy push-notification connector (rollout step 2 from ADR 018).

Per-user payload shape (sealed JSON):

    {
        "url":   "http://ntfy:80",     # optional; defaults to NTFY_URL
        "topic": "lumogis-alice",      # required for delivery
        "token": "tk_..."              # optional; sets Authorization header
    }

Resolution lives in :mod:`services.ntfy_runtime`. Under
``AUTH_ENABLED=true`` a missing row is ``connector_not_configured``;
the legacy ``NTFY_URL`` / ``NTFY_TOPIC`` / ``NTFY_TOKEN`` env vars are
only honored when ``AUTH_ENABLED=false`` (single-user dev).
"""


CALDAV = "caldav"
"""CalDAV calendar source connector (rollout step 3 from ADR 018).

Per-user payload shape (sealed JSON, all three fields required and
non-empty strings; extra keys tolerated for forward-compat with
future fields like ``auth_type``):

    {
        "base_url": "https://nextcloud.example.com/remote.php/dav/",
        "username": "alice",
        "password": "<secret>"
    }

``base_url`` MUST satisfy ``urllib.parse.urlparse(value).scheme in
{"http", "https"}`` (case-insensitive) AND non-empty ``netloc`` AND no
leading/trailing whitespace. Validated by
:func:`services.caldav_credentials._validate_payload` on every read.

Resolution lives in :mod:`services.caldav_credentials`. Under
``AUTH_ENABLED=true`` a missing row is ``connector_not_configured``;
the legacy ``CALENDAR_CALDAV_URL`` / ``CALENDAR_USERNAME`` /
``CALENDAR_PASSWORD`` env vars are only honored when
``AUTH_ENABLED=false`` (single-user dev). ``CALENDAR_LOOKAHEAD_HOURS``
remains a deployment-wide env in v1; per-user lookahead is a deferred
follow-up. ``CALENDAR_POLL_INTERVAL`` is legacy single-user only — the
canonical multi-user poll cadence is per-source ``sources.poll_interval``.
"""


# Cloud LLM vendor keys (rollout step 4 from ADR 018; plan
# llm_provider_keys_per_user_migration). One registered connector per
# distinct ``api_key_env`` value in ``config/models.yaml``. Payload shape
# (sealed JSON) for all six is:
#
#     {"api_key": "<vendor-secret>"}
#
# Runtime resolution lives in :mod:`services.llm_connector_map` (the
# env-string → connector-id translation) and :func:`config.get_llm_provider`
# (the per-user adapter cache + decrypt). Under ``AUTH_ENABLED=true`` a
# missing row is ``connector_not_configured`` (HTTP 424 at
# ``/v1/chat/completions``); the legacy
# ``ANTHROPIC_API_KEY`` / ``OPENAI_API_KEY`` / ``XAI_API_KEY`` /
# ``PERPLEXITY_API_KEY`` / ``GEMINI_API_KEY`` / ``MISTRAL_API_KEY`` env vars
# (and the corresponding ``app_settings`` rows written by the legacy
# ``PUT /api/v1/admin/settings`` ``api_keys`` body) are only honored when
# ``AUTH_ENABLED=false`` (single-user / legacy mode).
LLM_ANTHROPIC = "llm_anthropic"
"""Anthropic API key (Claude family). Payload: ``{"api_key": "<secret>"}``."""

LLM_OPENAI = "llm_openai"
"""OpenAI API key (ChatGPT / GPT-4o family). Payload: ``{"api_key": "<secret>"}``."""

LLM_XAI = "llm_xai"
"""xAI API key (Grok family). Payload: ``{"api_key": "<secret>"}``."""

LLM_PERPLEXITY = "llm_perplexity"
"""Perplexity API key. Payload: ``{"api_key": "<secret>"}``."""

LLM_GEMINI = "llm_gemini"
"""Google Gemini API key. Payload: ``{"api_key": "<secret>"}``."""

LLM_MISTRAL = "llm_mistral"
"""Mistral API key. Payload: ``{"api_key": "<secret>"}``."""


@dataclass(frozen=True)
class ConnectorSpec:
    """Canonical metadata for one registered connector.

    ``id`` mirrors the dict key in :data:`CONNECTORS`; the duplication is
    intentional so that ``CONNECTORS.values()`` is self-contained when
    iterated (no zip-with-keys gymnastics). The
    ``test_canonical_mapping_shape`` invariant test in
    ``tests/test_connectors_registry.py`` pins ``CONNECTORS[k].id == k``.

    ``description`` is the human-readable copy surfaced by
    ``GET /api/v1/me/connector-credentials/registry`` (plan
    ``credential_management_ux`` D2). MUST be a non-empty string;
    :func:`register` and the dataclass invariant test both enforce that.

    Future extension: new fields (``doc_url``, ``payload_schema_hint``,
    ``category``, ...) MUST be added with ``field(default=...)`` so
    existing in-tree entries don't need rewriting in the same patch.
    The wire shape returned by
    :func:`iter_registered_with_descriptions` is the explicit projection
    point — adding a field to :class:`ConnectorSpec` does NOT auto-leak
    it to the route.
    """

    id: str
    description: str


CONNECTORS: dict[str, ConnectorSpec] = {
    TESTCONNECTOR: ConnectorSpec(
        id=TESTCONNECTOR,
        description="Synthetic plumbing test (no real external system).",
    ),
    NTFY: ConnectorSpec(
        id=NTFY,
        description="ntfy push-notification connector — per-user url/topic/token.",
    ),
    CALDAV: ConnectorSpec(
        id=CALDAV,
        description="CalDAV calendar source — per-user base URL / username / password.",
    ),
    LLM_ANTHROPIC: ConnectorSpec(
        id=LLM_ANTHROPIC,
        description="Anthropic API key (Claude family). Per-user; payload {api_key}.",
    ),
    LLM_OPENAI: ConnectorSpec(
        id=LLM_OPENAI,
        description="OpenAI API key (ChatGPT / GPT-4o family). Per-user; payload {api_key}.",
    ),
    LLM_XAI: ConnectorSpec(
        id=LLM_XAI,
        description="xAI API key (Grok family). Per-user; payload {api_key}.",
    ),
    LLM_PERPLEXITY: ConnectorSpec(
        id=LLM_PERPLEXITY,
        description="Perplexity API key. Per-user; payload {api_key}.",
    ),
    LLM_GEMINI: ConnectorSpec(
        id=LLM_GEMINI,
        description="Google Gemini API key. Per-user; payload {api_key}.",
    ),
    LLM_MISTRAL: ConnectorSpec(
        id=LLM_MISTRAL,
        description="Mistral API key. Per-user; payload {api_key}.",
    ),
}
"""Canonical id → :class:`ConnectorSpec` mapping.

Single source of truth — adding a connector is one edit here. The
historical parallel structures (``REGISTERED_CONNECTORS`` set +
``CONNECTOR_DESCRIPTIONS`` dict) have been collapsed into this mapping
to remove the "registered without description" drift class.
:data:`REGISTERED_CONNECTORS` survives below as a derived frozenset
view for backward compat.
"""


REGISTERED_CONNECTORS: frozenset[str] = frozenset(CONNECTORS.keys())
"""Derived view of :data:`CONNECTORS`'s key set — kept for backward compat.

Existing call sites (``services.connector_credentials``,
``routes.connector_credentials``, ``routes.admin_diagnostics``, the
no-raw-user_id acceptance gate, downstream chunk plans) read this
attribute directly. It is rebound by :func:`register` to mirror the
canonical mapping.
"""


class UnknownConnector(ValueError):
    """Raised when a caller refers to an unregistered connector id."""


def validate_format(name: str) -> None:
    """Pure format check — does NOT verify registration.

    Raises ``ValueError`` (NOT :class:`UnknownConnector`) when ``name``
    is the wrong type, empty, too long, or contains characters outside
    ``[a-z0-9_]``. The registry-strictness model deliberately keeps
    format-check as a separate, always-enforced concern so the
    metadata-read / delete paths can drop the registration check
    without dropping SQL-safety bounds.
    """
    if not isinstance(name, str) or not name:
        raise ValueError("connector must be a non-empty string")
    if len(name) > CONNECTOR_MAX_LEN:
        raise ValueError(
            f"connector exceeds max length {CONNECTOR_MAX_LEN}: {len(name)}"
        )
    if not CONNECTOR_FORMAT.match(name):
        raise ValueError(
            f"connector must match {CONNECTOR_FORMAT.pattern}: {name!r}"
        )


def is_registered(name: str) -> bool:
    """Return True iff ``name`` is currently in the registry.

    Does not validate format first — pass a known-format string. Callers
    that want both checks should use :func:`require_registered`.
    """
    return name in CONNECTORS


def require_registered(name: str) -> None:
    """Format-check then registration-check.

    Raises ``ValueError`` on bad format and :class:`UnknownConnector`
    when the format is fine but the connector is not in the registry.
    Used by every plaintext-bearing or write service path
    (``put_payload``, ``get_payload``, ``resolve``).
    """
    validate_format(name)
    if name not in CONNECTORS:
        raise UnknownConnector(f"unknown connector: {name!r}")


def iter_registered_with_descriptions() -> list[dict[str, str]]:
    """Sorted (id ASC) list of ``{"id", "description"}`` dicts.

    The wire shape returned by
    ``GET /api/v1/me/connector-credentials/registry`` (plan
    ``credential_management_ux`` D2). Sourced directly from
    :data:`CONNECTORS` so the "registered without description" drift
    class is structurally impossible — every value is a
    :class:`ConnectorSpec` with a non-empty ``description`` enforced at
    construction (by :func:`register`) or at module import (by the
    canonical literal above; pinned by
    ``tests/test_connectors_registry.py::test_canonical_mapping_shape``).

    Stable sort by ``id`` ascending so the JSON output is byte-for-byte
    deterministic — useful for snapshot-style test diffs and for the UI
    dropdown's reading order.

    The wire projection is intentionally narrow: only ``id`` +
    ``description`` reach the client even if :class:`ConnectorSpec`
    grows future fields. Surfacing a new field is an explicit,
    reviewable edit here, not an automatic side effect of extending the
    dataclass.
    """
    return [
        {"id": spec.id, "description": spec.description}
        for spec in sorted(CONNECTORS.values(), key=lambda s: s.id)
    ]


def register(name: str, *, description: str) -> None:
    """Add a connector id + description at import time (e.g. from a plugin).

    ``description`` is keyword-only and required — there is no overload
    that lets a caller register a connector without metadata, by design.
    Raises ``ValueError`` on bad format or empty/whitespace
    ``description``.

    Idempotent on ``name`` — re-registering an existing id replaces its
    spec (covers the "fix a typo in the description" path without
    needing a separate update verb). Test fixtures that register a
    throwaway id should pop it from :data:`CONNECTORS` in their
    teardown to keep the global registry clean across tests.

    .. note::

       This rebinds the module-level :data:`REGISTERED_CONNECTORS`
       frozenset (frozensets are immutable). Callers that did
       ``from connectors.registry import REGISTERED_CONNECTORS`` will
       hold a stale reference. Use ``from connectors import registry``
       and call :func:`is_registered` / :func:`require_registered`.
    """
    validate_format(name)
    if not isinstance(description, str) or not description.strip():
        raise ValueError(
            "description must be a non-empty string; "
            "every registered connector needs human-readable copy for "
            "the GET /api/v1/me/connector-credentials/registry endpoint"
        )
    CONNECTORS[name] = ConnectorSpec(id=name, description=description)
    global REGISTERED_CONNECTORS
    REGISTERED_CONNECTORS = frozenset(CONNECTORS.keys())
