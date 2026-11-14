# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""``/api/v1/me/email-providers`` — the static email provider registry (LUM-644).

Read-only, non-secret provider host/port/app-password guidance for the guided
mailbox setup flow. The single source of truth (``services/email_providers.py``);
LUM-650 (SMTP send) reads the same registry rather than forking a host table.
"""

from __future__ import annotations

import os

from auth import UserContext
from authz import require_user
from fastapi import APIRouter
from fastapi import Depends
from models.email_setup import ProviderInfoModel
from models.email_setup import ProvidersResponse

from services import email_oauth
from services import email_providers

router = APIRouter(prefix="/api/v1/me", tags=["email-providers"])


def _to_model(p: email_providers.ProviderInfo) -> ProviderInfoModel:
    """Mirror a ``ProviderInfo`` to the wire model, computing the OAuth-dependent
    fields dynamically (LUM-687). A provider advertises ``supports_oauth`` only
    when its BYO ``client_id`` is configured; an **OAuth-only** provider (Outlook)
    whose client_id is unset is presented ``enabled=False`` with a setup hint —
    so an unconfigured install shows an honest disabled card, not a dead-end."""
    oauth_available = p.supports_oauth and email_oauth.has_oauth_provider(p.oauth_provider)
    enabled = p.enabled
    disabled_reason = p.disabled_reason
    if p.supports_oauth and p.auth_status == "oauth_only":
        enabled = oauth_available
        disabled_reason = None if oauth_available else p.disabled_reason
    return ProviderInfoModel(
        id=p.id,
        display_name=p.display_name,
        imap_host=p.imap_host,
        imap_port=p.imap_port,
        smtp_host=p.smtp_host,
        smtp_port=p.smtp_port,
        app_password_url=p.app_password_url,
        help_steps=list(p.help_steps),
        auth_status=p.auth_status,
        enabled=enabled,
        disabled_reason=disabled_reason,
        notes=p.notes,
        label_visible=p.label_visible,
        supports_oauth=oauth_available,
        oauth_provider=p.oauth_provider,
    )


def _operator_action_reason() -> str | None:
    """Why a mailbox added now would not sync until someone intervenes — or None.

    LUM-758. Two independent gates, reported in the order the member would hit them:

    * **ceiling** — ``tethered.activate(locked=True)`` bakes its allowlist at boot,
      so a host added afterwards is refused until Core restarts. That is LUM-636 and
      is not fixed here; the honest move is to say so up front.
    * **watch_disabled** — the proxy's allow-file watcher was switched off, which
      restores the operator-reload requirement. Core must read the *same* toggle the
      proxy does: if these two ever disagree, setup goes back to promising a sync
      that never happens, which is worse than the bug LUM-758 fixes because the UI
      now actively says everything is fine.
    """
    from services.egress_guard import egress_ceiling_enabled

    if egress_ceiling_enabled():
        return "ceiling"
    if os.environ.get("LUMOGIS_EGRESS_ALLOW_WATCH", "1") == "0":
        return "watch_disabled"
    return None


@router.get("/email-providers", response_model=ProvidersResponse)
def get_email_providers(user: UserContext = Depends(require_user)):
    reason = _operator_action_reason()
    return ProvidersResponse(
        providers=[_to_model(p) for p in email_providers.list_providers()],
        new_mailbox_needs_operator=reason is not None,
        operator_action_reason=reason,
    )
