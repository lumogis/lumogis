# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""``/api/v1`` aggregator for the Lumogis Web client façade.

Mounts only the **new** sub-routers introduced by the
``cross_device_lumogis_web`` plan. Shipped routers (``routes/auth.py``,
``routes/me.py``, ``routes/admin_users.py``, ``routes/admin.py``,
``routes/scope.py``, ``routes/connector_credentials.py``,
``routes/connector_permissions.py``, ``routes/mcp_tokens.py``) already
mount under ``/api/v1`` from :mod:`orchestrator.main` and are NOT
re-included here — doing so would double-register the same paths.

The aggregator does not own a prefix of its own — each sub-router pins
its full ``/api/v1/...`` prefix so they can be moved independently
later without renumbering the include.
"""

from __future__ import annotations

from fastapi import APIRouter

from .approvals import router as approvals_router
from .biography_conflicts import router as biography_conflicts_router
from .audit import router as audit_router
from .captures import router as captures_router
from .chat import router as chat_router
from .conversations import router as conversations_router
from .documents import router as documents_router
from .events import router as events_router
from .feature_flags import router as feature_flags_router
from .health import router as health_router
from .ingest import router as ingest_router
from .kg import router as kg_router
from .memory import router as memory_router
from .notification_preferences import admin_router as notification_prefs_admin_router
from .notification_preferences import me_router as notification_prefs_me_router
from .notifications import router as notifications_router
from .privacy_mode import router as privacy_mode_router
from .voice import router as voice_router

router = APIRouter(tags=["v1"])

router.include_router(chat_router)
router.include_router(conversations_router)
router.include_router(documents_router)
router.include_router(memory_router)
router.include_router(kg_router)
router.include_router(approvals_router)
router.include_router(biography_conflicts_router)
router.include_router(audit_router)
router.include_router(captures_router)
router.include_router(notifications_router)
router.include_router(notification_prefs_me_router)
router.include_router(notification_prefs_admin_router)
router.include_router(privacy_mode_router)
router.include_router(voice_router)
router.include_router(events_router)
router.include_router(feature_flags_router)
router.include_router(health_router)
router.include_router(ingest_router)

__all__ = ["router"]
