# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Per-user runtime loader for the paperless-ngx connector (LUM-281).

Mirrors :mod:`services.caldav_credentials` split:

* ``AUTH_ENABLED=true`` — credential row required; env vars ignored.
* ``AUTH_ENABLED=false`` — row wins; else ``PAPERLESS_BASE_URL`` +
  ``PAPERLESS_TOKEN`` for ``user_id="default"``.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from auth import auth_enabled
from connectors.registry import PAPERLESS
from services.outbound_http_url import validate_outbound_connector_base_url

from services import connector_credentials as ccs

_PAYLOAD_KEYS: tuple[str, str] = ("base_url", "token")


@dataclass(frozen=True)
class PaperlessConnection:
    """Resolved paperless REST base URL + API token for one user."""

    base_url: str
    token: str


def load_connection(
    user_id: str,
    *,
    resolve_host: Callable[[str], list[str]] | None = None,
) -> PaperlessConnection:
    """Load and validate the user's paperless connection.

    ``resolve_host`` is forwarded to
    :func:`services.outbound_http_url.validate_outbound_connector_base_url`,
    whose SSRF check resolves the host over DNS. That resolution is **untimed**
    by default, which is why the overlap probe (LUM-741) passes a
    deadline-bounded resolver: the probe advertises a ceiling
    (``PAPERLESS_OVERLAP_TIMEOUT_S``) that the credential load used to sit
    entirely outside of.

    Omitting it — every caller other than the overlap probe — keeps the default
    blocking resolver and the previous behaviour exactly.
    """
    payload = ccs.get_payload(user_id, PAPERLESS)

    if payload is not None:
        base_url, token = _validate_payload(payload, resolve_host=resolve_host)
        return PaperlessConnection(base_url=base_url, token=token)

    # LUM-834 chunk B — the disabled check comes BEFORE the auth split, and
    # this is the branch that made the whole toggle a no-op without it.
    #
    # `get_payload` returns None for a disabled row exactly as it does for an
    # absent one. Under AUTH_ENABLED=false — the DEFAULT — the code below
    # would then fall through to PAPERLESS_BASE_URL / PAPERLESS_TOKEN, so a
    # user who switched paperless off would have kept ingesting from the
    # env-configured server with no indication why.
    if ccs.is_disabled(user_id, PAPERLESS):
        raise ccs.disabled_error(PAPERLESS)

    if auth_enabled():
        raise ccs.ConnectorNotConfigured(f"no paperless credential row for user_id={user_id!r}")

    env_url = os.environ.get("PAPERLESS_BASE_URL", "")
    if not env_url:
        raise ccs.ConnectorNotConfigured(
            f"no paperless credential row for user_id={user_id!r} and no "
            "PAPERLESS_BASE_URL env fallback (AUTH_ENABLED=false)"
        )
    base_url = env_url
    token = os.environ.get("PAPERLESS_TOKEN", "")
    _validate_outbound_and_token(base_url, token, resolve_host=resolve_host)
    return PaperlessConnection(base_url=base_url, token=token)


def _validate_outbound_and_token(
    base_url: str,
    token: str,
    *,
    resolve_host: Callable[[str], list[str]] | None = None,
) -> None:
    validate_outbound_connector_base_url(base_url, resolve_host=resolve_host)
    if not token or not token.strip():
        raise ValueError("paperless token is empty")
    if token.strip() != token:
        raise ValueError("paperless token has leading/trailing whitespace")


def _validate_payload(
    payload: Any,
    *,
    resolve_host: Callable[[str], list[str]] | None = None,
) -> tuple[str, str]:
    if not isinstance(payload, dict):
        raise ccs.CredentialUnavailable("paperless payload must be a JSON object")

    values: dict[str, str] = {}
    for key in _PAYLOAD_KEYS:
        if key not in payload:
            raise ccs.CredentialUnavailable(f"paperless payload missing required key: {key!r}")
        value = payload[key]
        if not isinstance(value, str):
            raise ccs.CredentialUnavailable(f"paperless payload field {key!r} must be a string")
        if not value:
            raise ValueError(f"paperless payload field {key!r} is empty")
        if value.strip() != value:
            raise ValueError(f"paperless payload field {key!r} has leading/trailing whitespace")
        values[key] = value

    _validate_outbound_and_token(values["base_url"], values["token"], resolve_host=resolve_host)
    return values["base_url"], values["token"]
