# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""``GET /api/v1/me/shell`` — what the navigation shell shows (LUM-993).

One read-only answer to "which destinations does this user get?". Every flag is
computed from an existing local source (no network call, no write, no new
table) and in its **own** ``try``: a source that fails contributes ``false`` /
``0`` and a WARNING naming it, never a 500 — a navigation shell that cannot load
is worse than one that hides an item.

OpenAPI: no docstring on the handler (FastAPI copies it into the document).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TypeVar

from auth import UserContext
from authz import require_user
from fastapi import APIRouter
from fastapi import Depends
from models.api_v1 import MeShellState

import config

_log = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/me/shell",
    tags=["v1-me-shell"],
    dependencies=[Depends(require_user)],
)

# A capability scope is ``area:verb`` (``services.capability_scopes.SCOPE_PATTERN``).
# "Write scope" = the verb says the capability changes something outside Lumogis
# or in the user's data, so "Lumogis can act" is true. ``read`` / ``run`` /
# ``search`` style verbs are observation only and do not count.
_WRITE_VERBS = frozenset({"write", "send", "create", "update", "delete", "edit", "manage"})

_T = TypeVar("_T")


def _safe(source: str, fn: Callable[[], _T], fallback: _T) -> _T:
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001 - one failing source must not 500 the shell
        # Type name only: store errors can carry SQL or parameters in the message.
        _log.warning("me_shell: %s unavailable — %s", source, type(exc).__name__)
        return fallback


def is_write_scope(scope: str) -> bool:
    _, _, verb = str(scope).strip().partition(":")
    return verb in _WRITE_VERBS


def _email_connected(user: UserContext) -> bool:
    # Same rule as the web client's `isEmailActivated` over `GET /api/v1/sources`.
    from routes.signals import list_sources

    sources = list_sources(user).get("sources") or []
    return any(s.get("source_type") == "imap" for s in sources)


def _has_sending_mailbox(user_id: str) -> bool:
    from services import email_credentials

    return bool(email_credentials.sending_addresses(user_id))


def _has_healthy_write_capability() -> bool:
    # Cached health only (`RegisteredService.healthy`, set by the scheduled probe).
    for svc in config.get_capability_registry().all_services():
        if not svc.healthy:
            continue
        if any(is_write_scope(s) for s in (svc.manifest.permissions_required or [])):
            return True
    return False


def _pending_proposals_count(user_id: str) -> int:
    from services import action_proposals

    return len(action_proposals.list_pending(user_id))


def _recoverable_sends_count(user_id: str) -> int:
    from services import action_proposals

    return len(action_proposals.list_recoverable_sends(user_id))


def _voice_available() -> bool:
    return config.get_stt_backend() != "none"


def _online_ai_allowed(user_id: str) -> bool:
    from services import privacy_mode

    return privacy_mode.effective_privacy_mode(user_id).effective == (
        privacy_mode.InstancePrivacyMode.ALLOW_CLOUD
    )


def _knowledge_graph_on() -> bool:
    return config.get_graph_mode() != "disabled"


@router.get("", response_model=MeShellState)
def get_my_shell_state(
    user: UserContext = Depends(require_user),
) -> MeShellState:
    uid = user.user_id
    # Only what waits for the user: proposals to approve and sends to retry.
    # The denied-action list on the Approvals page is history (action_log rows
    # never clear), so counting it would keep "waiting" and Approvals up forever.
    pending = _safe("pending_proposals", lambda: _pending_proposals_count(uid), 0) + _safe(
        "recoverable_sends", lambda: _recoverable_sends_count(uid), 0
    )
    can_act = (
        _safe("sending_addresses", lambda: _has_sending_mailbox(uid), False)
        or _safe("capability_registry", _has_healthy_write_capability, False)
        or pending > 0
    )
    return MeShellState(
        email_connected=_safe("sources", lambda: _email_connected(user), False),
        can_act=can_act,
        pending_approvals=pending,
        voice_available=_safe("stt_backend", _voice_available, False),
        online_ai_allowed=_safe("privacy_mode", lambda: _online_ai_allowed(uid), False),
        knowledge_graph_on=_safe("graph_mode", _knowledge_graph_on, False),
    )
