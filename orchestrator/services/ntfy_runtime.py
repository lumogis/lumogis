# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Runtime config loader for the ntfy connector (ADR 027 tier walk).

Bridges the tiered credential stores with the legacy ``NTFY_URL`` /
``NTFY_TOPIC`` / ``NTFY_TOKEN`` env vars, applying ADR 018 D3:

* ``AUTH_ENABLED=true``  — a credential row is required at one of the
  ``user → household → system`` tiers. None present →
  :class:`ConnectorNotConfigured`. The legacy env vars are NOT consulted
  for ``topic`` or ``token``; an optional non-secret server ``url``
  defaults from ``NTFY_URL`` so households can keep one ntfy server
  location in compose without duplicating it across every payload.
* ``AUTH_ENABLED=false`` — single-user dev compatibility. A row at any
  tier wins; otherwise we assemble a config from the legacy env vars
  (matching pre-migration behavior).

**LUM-24 — why the tier walk is here and not just in the read model.**
This loader used to call ``ccs.get_payload(user_id, NTFY)`` directly,
consulting the per-user tier ONLY. Meanwhile
:func:`services.me_notifications._build_ntfy_channel` reports
``active_tier`` across all four tiers. The two disagreed: an admin could
write a household ntfy credential, every member's settings screen would
report ``configured=True, active_tier="household"``, and delivery would
raise :class:`ConnectorNotConfigured` — swallowed as ``status="skipped"``
by ``services.notifications.channels.ntfy_channel`` and as ``False`` by
``adapters.ntfy_notifier``, so the notification silently never arrived.
Reading through :func:`credential_tiers.resolve_runtime_credential`
is what keeps the read model and the send path telling the same story.

**Pause does not fall through.** ``delivery_paused`` lives only on the
per-user record (the household and system record types have no such
field). When the resolver lands on the ``user`` tier a paused row raises
rather than advancing to the household credential — pausing is a
deliberate act, and silently substituting the household credential would
defeat it. This mirrors the resolver's own rule that a decrypt failure
never falls through to the next tier.

Decrypt failures surface as :class:`CredentialUnavailable` from the
underlying service; this module re-raises without translation so the
adapter layer can map domain code → log line / response in one place.

Connector id ``ntfy`` is registered in :mod:`connectors.registry`;
the tier services enforce registry membership without any extra check
here.
"""

from __future__ import annotations

import logging
import os
from typing import TypedDict

from auth import auth_enabled
from connectors.registry import NTFY

from services import connector_credentials as ccs
from services import credential_tiers

_log = logging.getLogger(__name__)

_DEFAULT_URL = "http://ntfy:80"


class NtfyRuntimeConfig(TypedDict):
    """Resolved per-user ntfy delivery config."""

    url: str
    topic: str
    token: str


def load_ntfy_runtime_config(user_id: str) -> NtfyRuntimeConfig:
    """Return the ntfy delivery config for ``user_id``.

    Walks ``user → household → system`` via
    :func:`services.credential_tiers.resolve_runtime_credential`, then
    falls back to the legacy env vars under ``AUTH_ENABLED=false``.

    The env tier is handled here rather than through the resolver's
    ``fallback_env`` parameter: that helper returns the single-value
    shape ``{"value": ...}``, which cannot express ntfy's three
    independent env vars (``NTFY_URL`` / ``NTFY_TOPIC`` / ``NTFY_TOKEN``).

    Raises:
        ConnectorNotConfigured: under ``AUTH_ENABLED=true`` when no row
            exists at any tier, when the per-user row is paused, or
            under either auth mode when no row exists and the
            env-fallback path produced no usable ``topic``.
        CredentialUnavailable: propagated from the tier services on
            decrypt failure or malformed plaintext.
    """
    try:
        resolved = credential_tiers.resolve_runtime_credential(user_id, NTFY)
    except ccs.ConnectorNotConfigured:
        # "Nothing at any tier" — the env fallback below is the last
        # word. Decrypt failures (CredentialUnavailable) still propagate.
        resolved = None

    if resolved is not None:
        payload = resolved.payload
        if resolved.tier == "user":
            rec = ccs.get_record(user_id, NTFY)
            if rec is not None and rec.delivery_paused:
                raise ccs.ConnectorNotConfigured(
                    "ntfy delivery is paused pending credential update "
                    "(upstream returned gone); open Connectors to fix or clear."
                )
        topic = (payload.get("topic") or "").strip()
        if not topic:
            raise ccs.ConnectorNotConfigured(
                f"ntfy credential at tier={resolved.tier!r} for user_id={user_id!r} "
                "is missing the required 'topic' field"
            )
        url = (payload.get("url") or os.environ.get("NTFY_URL", _DEFAULT_URL)).rstrip("/")
        token = (payload.get("token") or "").strip()
        return NtfyRuntimeConfig(url=url, topic=topic, token=token)

    # LUM-834 chunk B — same shape as the delivery_paused check above, and for
    # the same reason: this loader reads ConnectorNotConfigured as "try the
    # env", which is right for absent and wrong for disabled. Without this,
    # switching ntfy off under the default AUTH_ENABLED=false would keep
    # delivering to NTFY_TOPIC / NTFY_URL.
    if ccs.is_disabled(user_id, NTFY):
        raise ccs.disabled_error(NTFY)

    if auth_enabled():
        raise ccs.ConnectorNotConfigured(
            f"no ntfy credential row at any tier (user/household/system) for user_id={user_id!r}"
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
