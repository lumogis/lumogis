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
from dataclasses import dataclass
from typing import Any

from auth import auth_enabled
from connectors.registry import PAPERLESS

from services import connector_credentials as ccs
from services.outbound_http_url import validate_outbound_connector_base_url

_PAYLOAD_KEYS: tuple[str, str] = ("base_url", "token")


@dataclass(frozen=True)
class PaperlessConnection:
    """Resolved paperless REST base URL + API token for one user."""

    base_url: str
    token: str


def load_connection(user_id: str) -> PaperlessConnection:
    payload = ccs.get_payload(user_id, PAPERLESS)

    if payload is not None:
        base_url, token = _validate_payload(payload)
        return PaperlessConnection(base_url=base_url, token=token)

    if auth_enabled():
        raise ccs.ConnectorNotConfigured(
            f"no paperless credential row for user_id={user_id!r}"
        )

    env_url = os.environ.get("PAPERLESS_BASE_URL", "")
    if not env_url:
        raise ccs.ConnectorNotConfigured(
            f"no paperless credential row for user_id={user_id!r} and no "
            "PAPERLESS_BASE_URL env fallback (AUTH_ENABLED=false)"
        )
    base_url = env_url
    token = os.environ.get("PAPERLESS_TOKEN", "")
    _validate_outbound_and_token(base_url, token)
    return PaperlessConnection(base_url=base_url, token=token)


def _validate_outbound_and_token(base_url: str, token: str) -> None:
    validate_outbound_connector_base_url(base_url)
    if not token or not token.strip():
        raise ValueError("paperless token is empty")
    if token.strip() != token:
        raise ValueError("paperless token has leading/trailing whitespace")


def _validate_payload(payload: Any) -> tuple[str, str]:
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

    _validate_outbound_and_token(values["base_url"], values["token"])
    return values["base_url"], values["token"]
