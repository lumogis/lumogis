# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Read-only ``GET /api/v1/me/notifications`` façade.

Surfaces **safe metadata** for notification channels:

* **ntfy** — registered connector ``ntfy``; tier walk matches
  :mod:`services.ntfy_runtime` / :mod:`services.credential_tiers` precedence
  for *row presence* only. **Does not** decrypt credential payloads: when a
  user row exists, topic/token/url presence inside the blob is reported as
  unknown (``null`` booleans) unless the legacy env-fallback path applies
  (``AUTH_ENABLED=false``), where only **non-secret** deployment URL and
  **booleans** for topic/token env vars are derived (values never returned).

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

    meta_rec: object | None
    if user_rec is not None:
        tier: Literal["user", "household", "system", "env", "none"] = "user"
        meta_rec = user_rec
    elif hh_rec is not None:
        tier = "household"
        meta_rec = hh_rec
    elif sys_rec is not None:
        tier = "system"
        meta_rec = sys_rec
    elif env_fb:
        tier = "env"
        meta_rec = None
    else:
        tier = "none"
        meta_rec = None

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
        status: Literal["configured", "not_configured"] = "configured"
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
        status: Literal["configured", "not_configured"] = "configured"
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
    n_cfg = sum(1 for c in channels if c.configured)
    by_tier: dict[str, int] = {}
    for c in channels:
        by_tier[c.active_tier] = by_tier.get(c.active_tier, 0) + 1

    return MeNotificationsResponse(
        channels=channels,
        summary=MeNotificationsSummary(
            total=total,
            configured=n_cfg,
            not_configured=total - n_cfg,
            by_active_tier=dict(sorted(by_tier.items())),
        ),
    )
