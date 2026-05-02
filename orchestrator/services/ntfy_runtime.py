# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Per-user runtime config loader for the ntfy connector.

Bridges :mod:`services.connector_credentials` (per-user encrypted JSON)
with the legacy ``NTFY_URL`` / ``NTFY_TOPIC`` / ``NTFY_TOKEN`` env
vars, applying ADR 018 D3:

* ``AUTH_ENABLED=true``  — a credential row for ``(user_id, "ntfy")``
  is required. Missing row → :class:`ConnectorNotConfigured`. The
  legacy env vars are NOT consulted for ``topic`` or ``token``; an
  optional non-secret server ``url`` defaults from ``NTFY_URL`` so
  households can keep one ntfy server location in compose without
  duplicating it across every user payload.
* ``AUTH_ENABLED=false`` — single-user dev compatibility. If a row
  exists it wins; otherwise we assemble a config from the legacy env
  vars (matching pre-migration behavior).

Decrypt failures surface as :class:`CredentialUnavailable` from the
underlying service; this module re-raises without translation so the
adapter layer can map domain code → log line / response in one place.

Connector id ``ntfy`` is registered in :mod:`connectors.registry`;
:func:`services.connector_credentials.get_payload` therefore enforces
registry membership without any extra check here.
"""

from __future__ import annotations

import logging
import os
from typing import TypedDict

from auth import auth_enabled
from services import connector_credentials as ccs
from connectors.registry import NTFY

_log = logging.getLogger(__name__)

_DEFAULT_URL = "http://ntfy:80"


class NtfyRuntimeConfig(TypedDict):
    """Resolved per-user ntfy delivery config."""

    url: str
    topic: str
    token: str


def load_ntfy_runtime_config(user_id: str) -> NtfyRuntimeConfig:
    """Return the ntfy delivery config for ``user_id``.

    Raises:
        ConnectorNotConfigured: under ``AUTH_ENABLED=true`` when no
            row exists, or under either auth mode when no row exists
            and the env-fallback path produced no usable ``topic``.
        CredentialUnavailable: propagated from
            :func:`services.connector_credentials.get_payload` on
            decrypt failure or malformed plaintext.
    """
    payload = ccs.get_payload(user_id, NTFY)

    if payload is not None:
        topic = (payload.get("topic") or "").strip()
        if not topic:
            raise ccs.ConnectorNotConfigured(
                f"ntfy credential row for user_id={user_id!r} is missing "
                "the required 'topic' field"
            )
        url = (payload.get("url") or os.environ.get("NTFY_URL", _DEFAULT_URL)).rstrip("/")
        token = (payload.get("token") or "").strip()
        return NtfyRuntimeConfig(url=url, topic=topic, token=token)

    if auth_enabled():
        raise ccs.ConnectorNotConfigured(
            f"no ntfy credential row for user_id={user_id!r}"
        )

    topic = os.environ.get("NTFY_TOPIC", "").strip()
    if not topic:
        raise ccs.ConnectorNotConfigured(
            f"no ntfy credential row for user_id={user_id!r} and no "
            "NTFY_TOPIC env fallback (AUTH_ENABLED=false)"
        )
    url = os.environ.get("NTFY_URL", _DEFAULT_URL).rstrip("/")
    token = os.environ.get("NTFY_TOKEN", "").strip()
    return NtfyRuntimeConfig(url=url, topic=topic, token=token)
