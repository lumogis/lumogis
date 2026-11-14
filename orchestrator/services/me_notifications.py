# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Read-only ``GET /api/v1/me/notifications`` façade.

Surfaces **safe metadata** for notification channels:

* **ntfy** — registered connector ``ntfy``; tier walk matches
  :mod:`services.ntfy_runtime` / :mod:`services.credential_tiers` precedence
  for *row presence* only, through
  :func:`services.credential_tiers.runtime_tier_from_records` — so a
  **switched-off** user row is reported not configured, and no household,
  system or env channel is reported in its place (LUM-850). **Does not**
  decrypt credential payloads: when a user row exists, topic/token/url
  presence inside the blob is reported as unknown (``null`` booleans) unless
  the legacy env-fallback path applies (``AUTH_ENABLED=false``), where only
  **non-secret** deployment URL and **booleans** for topic/token env vars are
  derived (values never returned).

* **web_push** — synthetic channel id ``web_push`` (not a registry connector);
  counts per-user rows in ``webpush_subscriptions`` and reports whether VAPID
  keys are set on the server. Does **not** expose endpoints or key material.

Does not send notifications or alter :func:`services.ntfy_runtime.load_ntfy_runtime_config`.
"""

from __future__ import annotations

import os
import re
from typing import Literal

from auth import auth_enabled
from connectors import registry as reg
from connectors.registry import NTFY
from models.api_v1 import MeNotificationChannelItem
from models.api_v1 import MeNotificationsResponse
from models.api_v1 import MeNotificationsSummary

import config
from services import connector_credentials as ccs
from services import credential_tiers as ct

_DEFAULT_NTFY_URL = "http://ntfy:80"

_DESC_BRACES = re.compile(r"\{[^}]*\}")
_PAYLOAD_WORD = re.compile(r"\bpayload\b", re.IGNORECASE)
_TRAIL_JUNK = re.compile(r"\s*[,;—\-]+\s*$")

# Synthetic channel id for browser Web Push (see ``webpush_subscriptions``).
WEB_PUSH_CHANNEL_ID = "web_push"


def notification_channel_ids() -> tuple[str, ...]:
    """Stable ordering: registry ntfy first, then web push façade id."""
    return (NTFY, WEB_PUSH_CHANNEL_ID)


def _safe_registry_description(connector_id: str) -> str:
    spec = reg.CONNECTORS.get(connector_id)
    if spec is None:
        return ""
    text = _DESC_BRACES.sub("", spec.description)
    text = _PAYLOAD_WORD.sub("credential", text)
    text = " ".join(text.split())
    text = _TRAIL_JUNK.sub("", text).strip()
    return text[:2000]


def _ntfy_env_fallback_available() -> bool:
    """True when single-user env path could supply ntfy (topic required)."""
    if auth_enabled():
        return False
    return bool(os.environ.get("NTFY_TOPIC", "").strip())


def _ntfy_env_url() -> str:
    return os.environ.get("NTFY_URL", _DEFAULT_NTFY_URL).rstrip("/")


def _vapid_configured() -> bool:
    return bool(
        os.environ.get("WEBPUSH_VAPID_PUBLIC_KEY") and os.environ.get("WEBPUSH_VAPID_PRIVATE_KEY")
    )


def _webpush_subscription_count(user_id: str) -> int:
    ms = config.get_metadata_store()
    row = ms.fetch_one(
        # SCOPE-EXEMPT: this façade returns only the authenticated caller's own subscription count.
        "SELECT COUNT(*) AS n FROM webpush_subscriptions WHERE user_id = %s",
        (user_id,),
    )
    if row is None or row.get("n") is None:
        return 0
    return int(row["n"])


def _build_ntfy_channel(user_id: str) -> MeNotificationChannelItem:
    user_rec = ccs.get_record(user_id, NTFY)
    hh_rec = ct.household_get_record(NTFY)
    sys_rec = ct.system_get_record(NTFY)
    env_fb = _ntfy_env_fallback_available()

    # LUM-850: the resolver's own walk. A switched-off user row stops it, so
    # neither a household/system key nor the auth-off env channel is reported as
    # active — ``ntfy_runtime`` refuses all three for a disabled row.
    walk = ct.runtime_tier_from_records(
        user_rec,
        household_present=hh_rec is not None,
        system_present=sys_rec is not None,
        env_fallback=env_fb,
    )
    tier: Literal["user", "household", "system", "env", "none"] = (
        "user" if walk == "disabled" else walk
    )
    # The winning row's metadata; env and none have no row.
    meta_rec = {"user": user_rec, "household": hh_rec, "system": sys_rec}.get(tier)

    # Checked before `delivery_paused`: a disabled row never reaches the pause
    # check in `ntfy_runtime` (the resolver raises first), and "switched off"
    # is the user's own act, so it is the reason to show.
    if walk == "disabled" and user_rec is not None:
        return MeNotificationChannelItem(
            connector=NTFY,
            label="ntfy",
            description=_safe_registry_description(NTFY),
            configured=False,
            active_tier="user",
            user_credential_present=True,
            household_credential_available=hh_rec is not None,
            system_credential_available=sys_rec is not None,
            env_fallback_available=env_fb,
            url=None,
            url_configured=None,
            topic_configured=None,
            token_configured=None,
            updated_at=user_rec.updated_at,
            key_version=user_rec.key_version,
            subscription_count=None,
            push_service_configured=None,
            status="not_configured",
            why_not_available=(
                "Your ntfy credential is switched off, so no notifications are sent "
                "through ntfy, and no household, system or server-configured ntfy is "
                "used instead. Switch it back on in Connectors."
            ),
        )

    if tier == "user" and user_rec is not None and user_rec.delivery_paused:
        return MeNotificationChannelItem(
            connector=NTFY,
            label="ntfy",
            description=_safe_registry_description(NTFY),
            configured=True,
            active_tier="user",
            user_credential_present=True,
            household_credential_available=hh_rec is not None,
            system_credential_available=sys_rec is not None,
            env_fallback_available=env_fb,
            url=None,
            url_configured=None,
            topic_configured=None,
            token_configured=None,
            updated_at=user_rec.updated_at,
            key_version=user_rec.key_version,
            subscription_count=None,
            push_service_configured=None,
            status="paused",
            why_not_available=(
                "ntfy delivery is paused after the upstream server returned Gone (410) "
                "for your topic. Open Connectors to update or re-save credentials."
            ),
        )

    configured = tier != "none"
    updated_at = getattr(meta_rec, "updated_at", None) if meta_rec is not None else None
    key_version = getattr(meta_rec, "key_version", None) if meta_rec is not None else None

    url: str | None = None
    url_configured: bool | None = None
    topic_configured: bool | None = None
    token_configured: bool | None = None

    if tier == "env":
        url = _ntfy_env_url()
        url_configured = True
        topic_configured = bool(os.environ.get("NTFY_TOPIC", "").strip())
        token_configured = bool(os.environ.get("NTFY_TOKEN", "").strip())
    elif tier in ("user", "household", "system"):
        # Encrypted blob — do not decrypt for this façade.
        url_configured = None
        topic_configured = None
        token_configured = None
    else:
        why = (
            "No ntfy credential at user, household, or system tier, "
            "and no legacy env fallback (requires NTFY_TOPIC when AUTH is off)."
        )
        return MeNotificationChannelItem(
            connector=NTFY,
            label="ntfy",
            description=_safe_registry_description(NTFY),
            configured=False,
            active_tier="none",
            user_credential_present=user_rec is not None,
            household_credential_available=hh_rec is not None,
            system_credential_available=sys_rec is not None,
            env_fallback_available=env_fb,
            url=None,
            url_configured=False,
            topic_configured=False,
            token_configured=False,
            updated_at=updated_at,
            key_version=key_version,
            subscription_count=None,
            push_service_configured=None,
            status="not_configured",
            why_not_available=why,
        )

    if configured:
        status = "configured"
        why_not = None
    else:
        status = "not_configured"
        why_not = None

    return MeNotificationChannelItem(
        connector=NTFY,
        label="ntfy",
        description=_safe_registry_description(NTFY),
        configured=configured,
        active_tier=tier,
        user_credential_present=user_rec is not None,
        household_credential_available=hh_rec is not None,
        system_credential_available=sys_rec is not None,
        env_fallback_available=env_fb,
        url=url,
        url_configured=url_configured,
        topic_configured=topic_configured,
        token_configured=token_configured,
        updated_at=updated_at,
        key_version=key_version,
        subscription_count=None,
        push_service_configured=None,
        status=status,
        why_not_available=why_not,
    )


def _build_web_push_channel(user_id: str) -> MeNotificationChannelItem:
    n_sub = _webpush_subscription_count(user_id)
    vapid = _vapid_configured()
    configured = n_sub > 0 and vapid
    tier: Literal["user", "household", "system", "env", "none"] = "user" if n_sub > 0 else "none"

    if configured:
        status = "configured"
        why_not = None
    elif n_sub > 0 and not vapid:
        status = "not_configured"
        why_not = "Subscription rows exist but server VAPID keys are not configured."
    else:
        status = "not_configured"
        why_not = "No browser push subscriptions for this account."

    return MeNotificationChannelItem(
        connector=WEB_PUSH_CHANNEL_ID,
        label="Web Push",
        description=(
            "Browser notifications via Web Push (VAPID). "
            "Subscribe from a client that calls /api/v1/notifications/subscribe."
        ),
        configured=configured,
        active_tier=tier,
        user_credential_present=False,
        household_credential_available=False,
        system_credential_available=False,
        env_fallback_available=False,
        url=None,
        url_configured=None,
        topic_configured=None,
        token_configured=None,
        updated_at=None,
        key_version=None,
        subscription_count=n_sub,
        push_service_configured=vapid,
        status=status,
        why_not_available=why_not,
    )


def build_me_notifications_response(user_id: str) -> MeNotificationsResponse:
    """Build curated notification channel status for ``user_id``."""
    channels = [_build_ntfy_channel(user_id), _build_web_push_channel(user_id)]
    total = len(channels)
    n_cfg = sum(1 for c in channels if c.status == "configured")
    n_pause = sum(1 for c in channels if c.status == "paused")
    n_nc = total - n_cfg - n_pause
    by_tier: dict[str, int] = {}
    for c in channels:
        by_tier[c.active_tier] = by_tier.get(c.active_tier, 0) + 1

    return MeNotificationsResponse(
        channels=channels,
        summary=MeNotificationsSummary(
            total=total,
            configured=n_cfg,
            not_configured=n_nc,
            paused=n_pause,
            by_active_tier=dict(sorted(by_tier.items())),
        ),
    )
