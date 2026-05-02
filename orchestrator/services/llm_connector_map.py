# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""LLM provider key resolution via the per-user connector credentials substrate.

Translates ``config/models.yaml``'s ``api_key_env`` strings into the
``llm_*`` connector ids registered in :mod:`connectors.registry` and
resolves a user's plaintext API key via
:mod:`services.connector_credentials`. Domain errors raised by the
substrate (``ConnectorNotConfigured`` / ``CredentialUnavailable``) are
**re-raised unchanged** so the chat route can map them to the
424/503 OpenAI-compatible envelope without per-vendor branching.

Single source of truth
----------------------
:data:`LLM_CONNECTOR_BY_ENV` is the **only** place where the env-string
→ connector-id mapping lives. ``models.yaml``, the dashboard, the
migration script, and ``config.py`` all derive from it (directly via
the helpers below, or transitively via the registered connector ids in
:mod:`connectors.registry`).

Adding a new vendor
-------------------
1. ``config/models.yaml``: add the model entry with ``api_key_env``.
2. ``connectors/registry.py``: add the ``LLM_<VENDOR>`` constant and
   ``CONNECTORS`` entry.
3. This module: add the env-string → connector-id pair to
   :data:`LLM_CONNECTOR_BY_ENV` and the human label to
   :data:`_VENDOR_LABEL_BY_CONNECTOR`.
4. Tests: extend ``tests/test_llm_connector_map.py`` if a new shape lands.

The drift guard ``test_mapping_covers_models_yaml_envs`` enforces the
first three steps stay in sync.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import config
from auth import auth_enabled
from connectors.registry import (
    LLM_ANTHROPIC,
    LLM_GEMINI,
    LLM_MISTRAL,
    LLM_OPENAI,
    LLM_PERPLEXITY,
    LLM_XAI,
)
from services import connector_credentials as ccs
from services.connector_credentials import (  # re-exported for callers
    ConnectorNotConfigured,
    CredentialUnavailable,
)

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# env-string → connector-id mapping (single source of truth)
# ---------------------------------------------------------------------------

LLM_CONNECTOR_BY_ENV: dict[str, str] = {
    "ANTHROPIC_API_KEY":  LLM_ANTHROPIC,
    "OPENAI_API_KEY":     LLM_OPENAI,
    "XAI_API_KEY":        LLM_XAI,
    "PERPLEXITY_API_KEY": LLM_PERPLEXITY,
    "GEMINI_API_KEY":     LLM_GEMINI,
    "MISTRAL_API_KEY":    LLM_MISTRAL,
}
"""Frozen at import time; new entries require the four-step add-a-vendor flow above."""


_VENDOR_LABEL_BY_CONNECTOR: dict[str, str] = {
    LLM_ANTHROPIC:  "Anthropic",
    LLM_OPENAI:     "OpenAI",
    LLM_XAI:        "xAI",
    LLM_PERPLEXITY: "Perplexity",
    LLM_GEMINI:     "Google Gemini",
    LLM_MISTRAL:    "Mistral",
}


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def connector_for_api_key_env(api_key_env: str) -> str | None:
    """Return the registered connector id for ``api_key_env``, or ``None``.

    A return of ``None`` lets ``is_model_enabled`` treat unknown future
    cloud envs as "not migrated yet" without crashing on import. They
    fall back to the legacy ``app_settings``/env path under
    ``AUTH_ENABLED=false`` and are treated as ``connector_not_configured``
    under ``AUTH_ENABLED=true``.
    """
    if not isinstance(api_key_env, str) or not api_key_env:
        return None
    return LLM_CONNECTOR_BY_ENV.get(api_key_env)


def vendor_label_for_connector(connector_id: str) -> str:
    """Return a human-readable vendor label (e.g. ``"Anthropic"``).

    Falls back to ``connector_id`` unchanged for unknown ids — defensive,
    should never happen because the connector id was just resolved from
    a known ``api_key_env``.
    """
    return _VENDOR_LABEL_BY_CONNECTOR.get(connector_id, connector_id)


def has_credential(user_id: str | None, api_key_env: str) -> bool:
    """Registry-strict existence check for a user's credential row.

    Uses :func:`connector_credentials.get_record` (no decrypt). Returns
    ``False`` for unmapped envs, ``user_id is None``, and missing rows.
    Used by :func:`config.is_model_enabled`.
    """
    connector = connector_for_api_key_env(api_key_env)
    if connector is None or not user_id:
        return False
    try:
        return ccs.get_record(user_id, connector) is not None
    except Exception:
        # get_record raises ValueError on bad-format connector ids; the
        # registered llm_* ids cannot fail the format check, but defence-
        # in-depth: an unexpected DB outage should not make every cloud
        # model appear enabled (false positive). Treat as "not present".
        _log.exception(
            "has_credential: unexpected error reading record user_id=%s connector=%s",
            user_id, connector,
        )
        return False


def get_user_credentials_snapshot(user_id: str | None) -> set[str]:
    """Single-query snapshot of the user's ``llm_*`` connector ids.

    Used by ``routes/chat.py::list_models`` to avoid N point queries per
    ``/v1/models`` call. Returns the empty set for ``user_id is None``.
    Issues exactly one parameterised SELECT against
    ``user_connector_credentials`` — pinned by
    ``tests/test_chat_route_llm_credential_errors.py::test_v1_models_uses_single_query_per_call``.

    Backslash-escapes the ``_`` literal in the LIKE pattern so a future
    connector id like ``llmbroker`` (no underscore) cannot accidentally
    match the ``llm_*`` namespace.
    """
    if not user_id:
        return set()
    ms = config.get_metadata_store()
    # SCOPE-EXEMPT: user_connector_credentials has no `scope` column — it is
    # a per-user secrets table by construction. Caller (`/v1/models`) reads
    # only the calling user's own rows; visible_filter() does not apply.
    try:
        rows = ms.fetch_all(
            "SELECT connector FROM user_connector_credentials "
            "WHERE user_id = %s AND connector LIKE 'llm\\_%%' ESCAPE '\\'",
            (user_id,),
        )
    except Exception:
        _log.exception(
            "get_user_credentials_snapshot: unexpected error for user_id=%s", user_id,
        )
        return set()
    return {r["connector"] for r in rows}


def effective_api_key(user_id: str | None, api_key_env: str) -> str:
    """Resolve the per-user (or env-fallback) API key as a stripped string.

    Branches on :func:`auth.auth_enabled`:

    * ``AUTH_ENABLED=true``: registry-strict ``resolve(user_id, connector)``;
      env fallback is forbidden (the substrate ignores ``fallback_env``
      under auth-on by design — see ``connector_credentials.resolve``).
      Reads ``payload["api_key"]``.
    * ``AUTH_ENABLED=false``: substrate may fall back to
      ``os.environ[api_key_env]`` and return ``{"value": <env-val>}``;
      reads ``payload["value"]``.

    Exception contract:

    * :class:`ConnectorNotConfigured` — no row, no usable env fallback,
      OR the resolved key is the empty string after ``.strip()`` (covers
      both the per-user-row branch and the env-fallback branch — operator-
      identical from the user's POV).
    * :class:`CredentialUnavailable` — Fernet decrypt failed (propagated
      unchanged from the substrate) OR the decrypted payload is
      structurally malformed (missing or non-string ``api_key`` /
      ``value``). Folding the malformed-payload case into the existing
      503 vocabulary keeps the chat-route try/except shape simple at
      exactly two domain types.

    Unmapped envs (``connector_for_api_key_env`` returns ``None``):

    * ``AUTH_ENABLED=false``: read directly from ``os.environ[api_key_env]``;
      empty-after-strip raises :class:`ConnectorNotConfigured`. Preserves
      legacy behaviour for cloud envs that have not been added to the
      registry yet.
    * ``AUTH_ENABLED=true``: raises :class:`ConnectorNotConfigured`
      immediately; without a registered connector there is no way to
      produce a per-user key.
    """
    connector = connector_for_api_key_env(api_key_env)

    if connector is None:
        if auth_enabled():
            raise ConnectorNotConfigured(
                f"no llm_* connector registered for api_key_env={api_key_env!r}"
            )
        env_val = os.environ.get(api_key_env, "").strip()
        if not env_val:
            raise ConnectorNotConfigured(
                f"env var {api_key_env!r} is unset or blank under AUTH_ENABLED=false"
            )
        return env_val

    payload = ccs.resolve(user_id or "", connector, fallback_env=api_key_env)
    # Substrate guarantees dict; defence-in-depth in case of programming error.
    if not isinstance(payload, dict):
        raise CredentialUnavailable(
            f"payload for connector={connector!r} is not a JSON object "
            f"(got {type(payload).__name__})"
        )

    # Auth-on per-user-row payload uses {"api_key": ...}; auth-off env-fallback
    # uses the substrate's documented {"value": ...} shape.
    payload_key = "value" if "value" in payload and not auth_enabled() else "api_key"
    try:
        api_key: Any = payload[payload_key]
    except KeyError as exc:
        raise CredentialUnavailable(
            f"payload for connector={connector!r} is missing required key "
            f"{payload_key!r}"
        ) from exc
    if not isinstance(api_key, str):
        raise CredentialUnavailable(
            f"payload[{payload_key!r}] for connector={connector!r} is not a string "
            f"(got {type(api_key).__name__})"
        )

    api_key = api_key.strip()
    if not api_key:
        raise ConnectorNotConfigured(
            f"resolved api_key for connector={connector!r} is empty after strip "
            f"(user_id={user_id!r}, env={api_key_env!r})"
        )
    return api_key
