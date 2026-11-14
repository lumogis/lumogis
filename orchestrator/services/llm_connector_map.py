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

from auth import auth_enabled
from connectors.registry import LLM_ANTHROPIC
from connectors.registry import LLM_GEMINI
from connectors.registry import LLM_MISTRAL
from connectors.registry import LLM_OPENAI
from connectors.registry import LLM_PERPLEXITY
from connectors.registry import LLM_XAI
from services.connector_credentials import ConnectorNotConfigured  # re-exported for callers
from services.connector_credentials import CredentialUnavailable  # re-exported for callers

from services import connector_credentials as ccs
from services import credential_tiers as ct

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# env-string → connector-id mapping (single source of truth)
# ---------------------------------------------------------------------------

LLM_CONNECTOR_BY_ENV: dict[str, str] = {
    "ANTHROPIC_API_KEY": LLM_ANTHROPIC,
    "OPENAI_API_KEY": LLM_OPENAI,
    "XAI_API_KEY": LLM_XAI,
    "PERPLEXITY_API_KEY": LLM_PERPLEXITY,
    "GEMINI_API_KEY": LLM_GEMINI,
    "MISTRAL_API_KEY": LLM_MISTRAL,
}
"""Frozen at import time; new entries require the four-step add-a-vendor flow above."""


_VENDOR_LABEL_BY_CONNECTOR: dict[str, str] = {
    LLM_ANTHROPIC: "Anthropic",
    LLM_OPENAI: "OpenAI",
    LLM_XAI: "xAI",
    LLM_PERPLEXITY: "Perplexity",
    LLM_GEMINI: "Google Gemini",
    LLM_MISTRAL: "Mistral",
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
    """Registry-strict availability check across the credential tiers.

    Uses the ``*_get_record`` readers (no decrypt) at ``user →
    household → system``. Returns ``False`` for unmapped envs,
    ``user_id is None``, rows missing at every tier, and a **disabled**
    user row. Used by :func:`config.is_model_enabled`.

    LUM-24: this is the *availability* half of the tier fix. Wiring only
    :func:`effective_api_key` to the resolver would let a household LLM
    key decrypt correctly at chat time while ``is_model_enabled`` still
    reported the model off — resolvable but unreachable.

    LUM-850: the walk is :func:`credential_tiers.runtime_tier_from_records`,
    the resolver's own order on metadata, so a disabled user row stops it
    exactly where the resolver raises — a household or system key does not
    bring the model back. The shared tiers are read only when the user tier
    is absent, as before.
    """
    connector = connector_for_api_key_env(api_key_env)
    if connector is None or not user_id:
        return False
    try:
        user_rec = ccs.get_record(user_id, connector)
        household = user_rec is None and ct.household_get_record(connector) is not None
        system = user_rec is None and not household and ct.system_get_record(connector) is not None
    except Exception:
        # get_record raises ValueError on bad-format connector ids; the
        # registered llm_* ids cannot fail the format check, but defence-
        # in-depth: an unexpected DB outage should not make every cloud
        # model appear enabled (false positive). Treat as "not present".
        _log.exception(
            "has_credential: unexpected error reading record user_id=%s connector=%s",
            user_id,
            connector,
        )
        return False
    tier = ct.runtime_tier_from_records(
        user_rec, household_present=household, system_present=system
    )
    return tier in ct.SERVING_TIERS


def get_user_credentials_snapshot(user_id: str | None) -> set[str]:
    """Snapshot of the ``llm_*`` connector ids available to ``user_id``.

    Used by ``routes/chat.py::list_models`` to avoid N point queries per
    ``/v1/models`` call. Returns the empty set for ``user_id is None``.

    **Cost contract (LUM-24).** Three constant-cost queries per call —
    one per tier — pinned by
    ``tests/test_llm_connector_map.py::test_snapshot_uses_single_query``.
    The property that matters is that the count does NOT scale with the
    number of models or connectors; it was one query when only the
    per-user tier was consulted, and adding household + system keeps it
    O(1). It must never become a point query per model.

    Household and system rows are keyed by ``(connector)`` alone, so they
    are identical for every member — which is exactly why they belong in
    this snapshot: without them a household LLM key resolves at chat time
    (see :func:`effective_api_key`) but its model is filtered out of
    ``/v1/models`` and never offered.

    **A disabled user row hides the model (LUM-850)**, household or system
    key notwithstanding: each connector goes through
    :func:`credential_tiers.runtime_tier_from_records`, the resolver's walk on
    metadata. That is why the per-user tier is read with
    :func:`connector_credentials.list_records` (whole records, ``enabled``
    included) rather than a ``WHERE enabled`` filter — filtering would turn a
    disabled row into an absent one and let the shared key through. It also
    keeps the SQL over ``user_connector_credentials`` inside the credential
    service. ``list_records`` returns every connector for the user (a handful
    of rows) and the ``llm_`` prefix is matched here, exactly, in Python.
    """
    if not user_id:
        return set()
    try:
        user_recs = {
            rec.connector: rec
            for rec in ccs.list_records(user_id)
            if rec.connector.startswith("llm_") and not rec.instance
        }
    except Exception:
        _log.exception(
            "get_user_credentials_snapshot: unexpected error for user_id=%s",
            user_id,
        )
        return set()

    # Household + system tiers. Each reader is one unparameterised list
    # query; failures degrade to "per-user only" rather than hiding the
    # user's own models behind a tier outage.
    shared: dict[str, set[str]] = {}
    for name, reader in (
        ("household", ct.household_list_records),
        ("system", ct.system_list_records),
    ):
        try:
            shared[name] = {rec.connector for rec in reader() if rec.connector.startswith("llm_")}
        except Exception:
            _log.exception(
                "get_user_credentials_snapshot: unexpected error reading %s for user_id=%s",
                reader.__name__,
                user_id,
            )
            shared[name] = set()

    candidates = set(user_recs) | shared["household"] | shared["system"]
    return {
        connector
        for connector in candidates
        if ct.runtime_tier_from_records(
            user_recs.get(connector),
            household_present=connector in shared["household"],
            system_present=connector in shared["system"],
        )
        in ct.SERVING_TIERS
    }


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

    # LUM-24: user → household → system → env. The env tier keeps the
    # `{"value": ...}` shape documented above, so this is a drop-in for
    # the former `ccs.resolve(...)` (per-user + env only).
    #
    # LUM-834 chunk B — there is deliberately NO `is_disabled` guard in this
    # function, and the absence is the design rather than an oversight.
    #
    # The other five env-fallback branches in this codebase read `get_payload`
    # and fall through to an env var themselves, so each needs its own check.
    # This one does not: for a real user it delegates to
    # `resolve_runtime_credential`, whose tier-1 second read raises before its
    # tier-4 env fallback is ever consulted. The two env reads that DO live
    # here are unguardable by construction — the `connector is None` branch
    # above has no registered connector to ask about, and the `else` branch
    # below has no user, so a per-user flag has no meaning.
    #
    # llm's protection is therefore the tier-1 check. Adding a redundant guard
    # here would also break the mutation-check pair, which relies on "remove
    # one loader guard" and "remove the tier-1 check" being distinct edits.
    if user_id:
        payload = ct.resolve_runtime_credential(
            user_id, connector, fallback_env=api_key_env
        ).payload
    else:
        # No user context — the auth-off global slot
        # (``config.get_llm_provider(..., user_id=None)``). The resolver
        # rejects an empty ``caller_user_id`` outright rather than
        # quietly querying for one, so the shared tiers are walked
        # explicitly here. Household and system rows are keyed by
        # ``(connector)`` alone and so are perfectly well-defined
        # without a caller; only the per-user tier is meaningless.
        payload = ct.household_get_payload(connector)
        if payload is None:
            payload = ct.system_get_payload(connector)
        if payload is None:
            # Raises ConnectorNotConfigured when the env is unusable too,
            # which is the terminal behaviour callers already expect.
            payload = ccs.resolve("", connector, fallback_env=api_key_env)
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
            f"payload for connector={connector!r} is missing required key {payload_key!r}"
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
