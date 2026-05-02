# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Strictly-private internals shared by the credential service modules.

CONTRIBUTORS NOTE
-----------------
This module is **strictly private** to the credential subsystem. The
ONLY callers permitted are:

* :mod:`services.connector_credentials` (per-user tier)
* :mod:`services.credential_tiers`      (household + system tiers)

Importing :mod:`services._credential_internals` from any other module
is a code-review block. The leading underscore in the module name is
a convention; there is no project lint rule that enforces this today.
If a future caller genuinely needs a helper from here, request a
public surface change rather than reaching across the underscore.

Why this module exists
----------------------
The per-user credential service (ADR ``per_user_connector_credentials``,
migration 015) and the new household + instance/system tiers (ADR
``credential_scopes_shared_system``, migration 018) share the same
crypto, the same audit substrate, the same key fingerprint scheme, and
the same actor-string regex. Splitting the helpers into this private
module avoids duplicating the bytes — and avoids the inevitable drift
that would follow.

Surface (every name is "public to the package"):

* :data:`_PLACEHOLDER_KEYS`, :data:`_ACTOR_RE`, :data:`_ACTOR_RE_TIERED`
* :class:`ConnectorNotConfigured`, :class:`CredentialUnavailable`
* :func:`_load_keys`, :func:`_build_multifernet`, :func:`_get_multifernet`
* :func:`reset_for_tests`
* :func:`_key_fingerprint`, :func:`_current_key_version`,
  :func:`get_current_key_version`
* :func:`_encrypt_payload`, :func:`_decrypt_payload`
* :func:`_actor_str`, :func:`_actor_str_tiered`
* :func:`_emit_audit` — adds a **required** ``tier`` keyword arg
* :func:`_resolve_env_fallback` — single canonical implementation for
  both :func:`services.connector_credentials.resolve` and
  :func:`services.credential_tiers.resolve_runtime_credential`
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
from datetime import datetime
from datetime import timezone
from typing import Any
from typing import Literal

from actions.audit import write_audit
from auth import auth_enabled
from cryptography.fernet import Fernet
from cryptography.fernet import InvalidToken
from cryptography.fernet import MultiFernet
from models.actions import AuditEntry

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Sentinels rejected anywhere a credential key is read. Mirrors the
# ``AUTH_SECRET`` sentinel set used by ``main._enforce_auth_consistency``.
# ---------------------------------------------------------------------------

_PLACEHOLDER_KEYS: frozenset[str] = frozenset(
    {
        "",
        "change-me-in-production",
        "__GENERATE_ME__",
    }
)


# ---------------------------------------------------------------------------
# Actor format. Tightened from the table-level CHECK ``LIKE 'admin:%'``
# to a regex requiring 1..64 safe chars after the prefix.
#
# ``_ACTOR_RE`` accepts ``self|system|admin:<id>`` — used by the per-user
# table, which carries user-owned rows where ``self`` is meaningful.
#
# ``_ACTOR_RE_TIERED`` accepts ``system|admin:<id>`` only — used by the
# household + instance/system tier tables, which are NEVER user-owned
# and where ``self`` is therefore not a valid actor literal. Mirrors
# the Postgres CHECK constraint in migration 018 byte-for-byte.
# ---------------------------------------------------------------------------

_ACTOR_RE = re.compile(r"^(self|system|admin:[A-Za-z0-9_\-]{1,64})$")
_ACTOR_RE_TIERED = re.compile(r"^(system|admin:[A-Za-z0-9_\-]{1,64})$")


# ---------------------------------------------------------------------------
# Domain exceptions — shared so both the per-user and tier services raise
# the same exception classes; route handlers can match on a single name.
# ---------------------------------------------------------------------------


class ConnectorNotConfigured(LookupError):
    """Domain code ``connector_not_configured``.

    Raised by the runtime resolvers (``resolve`` and
    ``resolve_runtime_credential``) and by route handlers that translate
    a ``None`` from ``get_payload`` / ``get_record`` into the 404 path.
    The lower-level reads themselves return ``None`` on a missing row;
    they never raise this directly.
    """


class CredentialUnavailable(RuntimeError):
    """Domain code ``credential_unavailable``.

    Raised on :class:`cryptography.fernet.InvalidToken`, decrypt
    failure, or malformed JSON in plaintext. Route layer maps to HTTP
    503. The runtime resolver raises this immediately on decrypt
    failure on **any** tier — it does NOT fall through to the next
    tier (privilege-escalation-by-tampering guard).
    """


# ---------------------------------------------------------------------------
# Key loading + MultiFernet cache.
# ---------------------------------------------------------------------------


_CACHE_LOCK = threading.Lock()
_MF_CACHE: MultiFernet | None = None


def _load_keys() -> list[bytes]:
    """Return the ordered list of raw Fernet key bytes.

    Resolution rule:

    1. If ``LUMOGIS_CREDENTIAL_KEYS`` is set and parses to at least one
       usable key, use it (CSV, newest first).
    2. Otherwise fall back to a single-element list from
       ``LUMOGIS_CREDENTIAL_KEY``.

    Empty entries, whitespace-only entries, and the placeholder
    sentinels (``change-me-in-production`` / ``__GENERATE_ME__``) are
    stripped/rejected.

    Raises :class:`RuntimeError` whenever no usable key is found —
    **regardless of** ``auth_enabled()``. The boot-time refusal in
    ``main._enforce_auth_consistency`` is a separate, additional check
    that runs only under ``AUTH_ENABLED=true``.
    """
    raw_csv = os.environ.get("LUMOGIS_CREDENTIAL_KEYS", "")
    if raw_csv.strip():
        candidates = [k.strip() for k in raw_csv.split(",")]
    else:
        candidates = [os.environ.get("LUMOGIS_CREDENTIAL_KEY", "").strip()]

    keys: list[bytes] = []
    for cand in candidates:
        if cand in _PLACEHOLDER_KEYS:
            continue
        keys.append(cand.encode("ascii"))

    if not keys:
        raise RuntimeError(
            "LUMOGIS_CREDENTIAL_KEY[S] is unset, blank, or a placeholder. "
            "Generate a Fernet key with: "
            'python3 -c "from cryptography.fernet import Fernet; '
            'print(Fernet.generate_key().decode())" '
            "and set LUMOGIS_CREDENTIAL_KEY (or LUMOGIS_CREDENTIAL_KEYS, CSV "
            "newest-first, for rotation). The key is intentionally NOT "
            "auto-rotated — losing it makes every encrypted credential row "
            "in user_connector_credentials, household_connector_credentials, "
            "and instance_system_connector_credentials unrecoverable."
        )
    return keys


def _build_multifernet(keys: list[bytes]) -> MultiFernet:
    """Build a fresh MultiFernet from raw key bytes."""
    try:
        return MultiFernet([Fernet(k) for k in keys])
    except (ValueError, TypeError) as exc:
        raise RuntimeError(
            f"LUMOGIS_CREDENTIAL_KEY[S] contains a key that Fernet rejected "
            f"({exc.__class__.__name__}): regenerate with "
            'python3 -c "from cryptography.fernet import Fernet; '
            'print(Fernet.generate_key().decode())"'
        ) from exc


def _get_multifernet() -> MultiFernet:
    """Return the process-cached :class:`MultiFernet` instance.

    Single cache is shared across the per-user table and the
    household / instance-system tier tables: all three seal under the
    same ``LUMOGIS_CREDENTIAL_KEY[S]`` family.
    """
    global _MF_CACHE
    if _MF_CACHE is not None:
        return _MF_CACHE
    with _CACHE_LOCK:
        if _MF_CACHE is None:
            _MF_CACHE = _build_multifernet(_load_keys())
        return _MF_CACHE


def reset_for_tests() -> None:
    """Flush the process-scoped MultiFernet cache.

    Call after ``monkeypatch.setenv("LUMOGIS_CREDENTIAL_KEY[S]", ...)``
    in tests so the next encrypt/decrypt picks up the new key.
    """
    global _MF_CACHE
    with _CACHE_LOCK:
        _MF_CACHE = None


# ---------------------------------------------------------------------------
# Fingerprint helpers — stable per-key 32-bit unsigned ids.
# ---------------------------------------------------------------------------


def _key_fingerprint(key_bytes: bytes) -> int:
    """Return the stable 32-bit unsigned fingerprint of a Fernet key.

    ``int.from_bytes(hashlib.sha256(key_bytes).digest()[:4], "big")``.
    Range ``0 .. 2**32 - 1``. Stored in Postgres as ``BIGINT`` (signed
    64-bit) NOT ``INTEGER`` (signed 32-bit) because the unsigned 32-bit
    range overflows ``INTEGER`` for any fingerprint with the top bit
    set (~50% of all valid keys).
    """
    return int.from_bytes(hashlib.sha256(key_bytes).digest()[:4], "big")


def _current_key_version() -> int:
    """Fingerprint of the current primary (newest) key."""
    return _key_fingerprint(_load_keys()[0])


def get_current_key_version() -> int:
    """Public diagnostic helper — fingerprint of the current primary key."""
    return _current_key_version()


# ---------------------------------------------------------------------------
# Actor validation.
# ---------------------------------------------------------------------------


def _actor_str(actor: str) -> str:
    """Validate a per-user actor literal (``self|system|admin:<id>``)."""
    if not isinstance(actor, str) or not _ACTOR_RE.match(actor):
        raise ValueError(f"actor must match {_ACTOR_RE.pattern}: {actor!r}")
    return actor


def _actor_str_tiered(actor: str) -> str:
    """Validate a tier-table actor literal (``system|admin:<id>``).

    Rejects ``self`` (no user owns these rows) and any malformed value.
    Callers SHOULD log a structured WARN before letting the
    ``ValueError`` propagate so operators can trace mis-callers.
    """
    if not isinstance(actor, str) or not _ACTOR_RE_TIERED.match(actor):
        raise ValueError(f"actor must be 'system' or 'admin:<id>'; got {actor!r}")
    return actor


# ---------------------------------------------------------------------------
# Audit emission. Now requires a `tier` keyword so every audit row
# carries a stable discriminator inside `input_summary` JSON. Per-user
# callers pass tier="user"; tier-table callers pass "household" / "system".
# ---------------------------------------------------------------------------


def _emit_audit(
    user_id: str,
    connector: str,
    action_name: str,
    *,
    actor: str,
    key_version: int,
    tier: Literal["user", "household", "system"],
    ok: bool = True,
) -> None:
    """Write a single ``audit_log`` row for a credential lifecycle event.

    NEVER logs ciphertext or plaintext: ``input_summary`` carries only
    ``{actor, key_version, tier}``; ``result_summary`` carries only
    ``{ok}``. Audit failures are caught and logged at exception level,
    never re-raised — a missed audit row is a hygiene loss; the
    underlying mutation has already succeeded.

    ``user_id`` semantics depend on the tier:

    * ``tier="user"`` — the target user the row belongs to (existing
      semantics; preserved exactly).
    * ``tier="household"`` / ``tier="system"`` — the **acting admin's**
      ``user_id`` (parsed from the ``admin:<id>`` actor string), or the
      literal ``"default"`` for ``actor="system"`` (rotation script,
      migrations). Parsing happens at the tier-service call site via
      :func:`services.credential_tiers._extract_admin_caller_user_id`.
    """
    try:
        write_audit(
            AuditEntry(
                action_name=action_name,
                connector=connector,
                mode="DO",
                input_summary=json.dumps(
                    {"actor": actor, "key_version": key_version, "tier": tier},
                    default=str,
                ),
                result_summary=json.dumps({"ok": bool(ok)}, default=str),
                executed_at=datetime.now(timezone.utc),
                user_id=user_id,
            )
        )
    except Exception:
        _log.exception("audit write for %s failed", action_name)


# ---------------------------------------------------------------------------
# Crypto.
# ---------------------------------------------------------------------------


def _encrypt_payload(payload: dict[str, Any]) -> bytes:
    """JSON-encode then encrypt a payload with the current MultiFernet."""
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return _get_multifernet().encrypt(encoded)


def _decrypt_payload(ciphertext: bytes) -> dict[str, Any]:
    """Decrypt + JSON-parse a stored ciphertext.

    Decrypt failure or malformed JSON ⇒ :class:`CredentialUnavailable`.
    Successful decrypt of a non-dict payload also raises (the public
    contract is "JSON object" — anything else is a bug or tampering).
    """
    mf = _get_multifernet()
    try:
        plaintext = mf.decrypt(bytes(ciphertext))
    except InvalidToken as exc:
        raise CredentialUnavailable(
            "decrypt failed (InvalidToken); ciphertext bytes NOT logged"
        ) from exc
    try:
        obj = json.loads(plaintext.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CredentialUnavailable(
            f"plaintext is not valid JSON ({exc.__class__.__name__})"
        ) from exc
    if not isinstance(obj, dict):
        raise CredentialUnavailable(f"plaintext is not a JSON object (got {type(obj).__name__})")
    return obj


# ---------------------------------------------------------------------------
# Env fallback — the SINGLE canonical implementation used by
# `services.connector_credentials.resolve()` AND
# `services.credential_tiers.resolve_runtime_credential()`. Eliminates
# the byte-for-byte mirror drift risk noted in the plan §D1.7.
# ---------------------------------------------------------------------------


def _resolve_env_fallback(
    connector: str,
    fallback_env: str | None,
) -> dict[str, Any] | None:
    """Return the env-fallback payload, or ``None`` when not applicable.

    Rules (identical for both resolvers):

    * ``AUTH_ENABLED=true`` ⇒ env fallback is **forbidden**. If a
      ``fallback_env`` was passed it is silently ignored after one
      DEBUG log line (so consumers see the misuse without paying the
      cost of an exception). Returns ``None`` so the caller raises
      :class:`ConnectorNotConfigured` itself.
    * ``AUTH_ENABLED=false`` and ``fallback_env`` is a non-empty string
      and ``os.environ[fallback_env]`` is non-empty ⇒ return
      ``{"value": env_str}`` (the **single permitted shape** for
      env-fallback so consumers always know what they got).
    * Otherwise ⇒ ``None``.

    ``connector`` is taken for the DEBUG log only — the helper does
    NOT validate registry membership (the caller already did so).
    """
    if auth_enabled():
        if fallback_env:
            _log.debug(
                "credential resolve: ignoring fallback_env=%r under "
                "AUTH_ENABLED=true (connector=%s)",
                fallback_env,
                connector,
            )
        return None

    if isinstance(fallback_env, str) and fallback_env:
        env_val = os.environ.get(fallback_env, "")
        if env_val:
            return {"value": env_val}

    return None
