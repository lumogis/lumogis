# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Per-user runtime config loader for the CalDAV connector.

Bridges :mod:`services.connector_credentials` (per-user encrypted JSON)
with the legacy ``CALENDAR_CALDAV_URL`` / ``CALENDAR_USERNAME`` /
``CALENDAR_PASSWORD`` env vars, applying the locked-decision contract
from ``.cursor/plans/caldav_connector_credentials.plan.md``:

* ``AUTH_ENABLED=true``  — a credential row for ``(user_id, "caldav")``
  is required. Missing row → :class:`ConnectorNotConfigured`. The
  legacy env vars are NOT consulted (D9 fail-loud, no auto-migrator).
* ``AUTH_ENABLED=false`` — single-user dev compatibility. If a row
  exists it wins; otherwise the legacy env trio is read and treated
  as the ``"default"`` user's connection (D10).

The split between :class:`ConnectorNotConfigured`,
:class:`CredentialUnavailable`, and ``ValueError`` is deliberate and
documented inline so the adapter layer can map domain code → log line
in one place (per ADR 018 D6 conflation ban):

* :class:`ConnectorNotConfigured` — no row + no env fallback usable.
  Adapter logs ``code=connector_not_configured`` and skips the poll.
* :class:`CredentialUnavailable` — substrate decrypt failed OR
  payload is structurally wrong (non-dict, missing key, non-string).
  Adapter logs ``code=credential_unavailable`` and skips the poll.
* ``ValueError`` — payload is structurally fine but a required string
  is empty / ``base_url`` fails the URL-shape rule (D11). Adapter
  catches and logs ``code=credential_unavailable`` (same code as the
  decrypt-side because both mean "row exists but unusable" — never
  ``connector_not_configured`` because a row IS present).

Connector id ``caldav`` is registered in :mod:`connectors.registry`;
:func:`services.connector_credentials.get_payload` therefore enforces
registry membership without any extra check here. If the registry
wiring ever regresses and ``caldav`` is dropped, ``get_payload``
raises :class:`connectors.registry.UnknownConnector`; this module
lets that propagate so the adapter's defensive ``except`` can map it
to a structured warning. ``test_caldav_is_registered`` is the
boot-time guard that should make this case unreachable in production.

Pattern precedent: :mod:`services.ntfy_runtime` (rollout step 2 from
ADR 018). This module mirrors its shape, swapping the field set.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from auth import auth_enabled
from connectors.registry import CALDAV
from services import connector_credentials as ccs


_PAYLOAD_KEYS: tuple[str, str, str] = ("base_url", "username", "password")
"""Wire contract for the encrypted CalDAV payload (v1).

All three keys are required and MUST be non-empty strings. Extra keys
at the top level are tolerated for forward-compat with future fields
like ``auth_type`` — :func:`_validate_payload` does NOT raise on
unknown keys.
"""


@dataclass(frozen=True)
class CaldavConnection:
    """Resolved per-user CalDAV connection parameters.

    Returned by :func:`load_connection` and consumed by
    :class:`adapters.calendar_adapter.CalendarAdapter`. Exists as a
    distinct shape (rather than passing the raw ``dict``) so the
    adapter does not have to re-validate keys on every poll and so
    payload values stay out of repr/log surfaces by accident
    (``frozen=True`` + a small named field set make accidental
    interpolation visible at code-review time).
    """

    base_url: str
    username: str
    password: str


def load_connection(user_id: str) -> CaldavConnection:
    """Return the CalDAV connection params for ``user_id``.

    Raises:
        ConnectorNotConfigured: under ``AUTH_ENABLED=true`` when no
            row exists, or under ``AUTH_ENABLED=false`` when no row
            exists and ``CALENDAR_CALDAV_URL`` env is empty.
        CredentialUnavailable: payload is structurally wrong
            (non-dict, missing key, non-string field), or propagated
            from :func:`services.connector_credentials.get_payload`
            on decrypt failure.
        ValueError: payload structurally fine but a required string
            is empty, or ``base_url`` fails the D11 URL-shape rule
            (scheme allowlist + non-empty netloc + no whitespace).
    """
    payload = ccs.get_payload(user_id, CALDAV)

    if payload is not None:
        base_url, username, password = _validate_payload(payload)
        return CaldavConnection(
            base_url=base_url,
            username=username,
            password=password,
        )

    if auth_enabled():
        raise ccs.ConnectorNotConfigured(
            f"no caldav credential row for user_id={user_id!r}"
        )

    env_url = os.environ.get("CALENDAR_CALDAV_URL", "")
    if not env_url:
        raise ccs.ConnectorNotConfigured(
            f"no caldav credential row for user_id={user_id!r} and no "
            "CALENDAR_CALDAV_URL env fallback (AUTH_ENABLED=false)"
        )
    return CaldavConnection(
        base_url=env_url,
        username=os.environ.get("CALENDAR_USERNAME", ""),
        password=os.environ.get("CALENDAR_PASSWORD", ""),
    )


def _validate_payload(payload: Any) -> tuple[str, str, str]:
    """Enforce the v1 wire contract on a decrypted payload.

    Returns ``(base_url, username, password)`` on success.

    Raises :class:`CredentialUnavailable` for *structural* errors
    (payload not a dict, required key missing, value not a string) so
    they map identically to substrate decrypt failures at the adapter
    layer. Raises ``ValueError`` for *content* errors (empty string,
    ``base_url`` failing the D11 URL rule, leading/trailing
    whitespace) so they can be distinguished if a future runtime
    ``test-connection`` route wants a different HTTP code (deferred
    per Open question #3).

    Tolerates unknown top-level keys (forward-compat for future
    fields like ``auth_type``).
    """
    if not isinstance(payload, dict):
        raise ccs.CredentialUnavailable(
            "caldav payload must be a JSON object"
        )

    values: dict[str, str] = {}
    for key in _PAYLOAD_KEYS:
        if key not in payload:
            raise ccs.CredentialUnavailable(
                f"caldav payload missing required key: {key!r}"
            )
        value = payload[key]
        if not isinstance(value, str):
            raise ccs.CredentialUnavailable(
                f"caldav payload field {key!r} must be a string"
            )
        if not value:
            raise ValueError(
                f"caldav payload field {key!r} is empty"
            )
        if value.strip() != value:
            raise ValueError(
                f"caldav payload field {key!r} has leading/trailing whitespace"
            )
        values[key] = value

    parsed = urlparse(values["base_url"])
    scheme = (parsed.scheme or "").lower()
    if scheme not in {"http", "https"}:
        raise ValueError(
            "caldav base_url must use scheme http or https"
        )
    # ``parsed.netloc`` of ``"https:// /dav"`` is the literal string
    # ``" "`` — non-empty per ``str``, but functionally hostless. The
    # ``.strip()`` rejects whitespace-only netlocs without inviting
    # the validator to second-guess otherwise-valid hostnames.
    if not parsed.netloc.strip():
        raise ValueError(
            "caldav base_url must include a non-empty host"
        )

    return values["base_url"], values["username"], values["password"]
