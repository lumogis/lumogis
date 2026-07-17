# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Middleware guard for live MCP / LLM tool results (LUM-362).

Applied from :func:`services.tools.run_tool` so every connector result is
scanned before it re-enters the LLM context. Flagged results are redacted for
the model; the raw payload is preserved in ``audit_log`` for forensics.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from datetime import timezone

import hooks
from actions.audit import write_audit
from events import Event
from models.actions import AuditEntry
from services.tool_result_scanner import USER_NOTIFICATION
from services.tool_result_scanner import scan_tool_result

import config

_log = logging.getLogger(__name__)

_TOOL_RESULT_FLAGGED_ACTION = "__injection__.tool_result_flagged"
# Keep audit rows bounded while preserving enough context for review.
_RAW_AUDIT_MAX_CHARS = 16_000


def guard_tool_result(raw: str, *, user_id: str, tool_name: str) -> str:
    """Scan *raw* tool output; return injection-safe text for the LLM loop."""

    if not raw or not config.is_tool_result_scanner_enabled():
        return raw

    outcome = scan_tool_result(raw)
    if not outcome["flagged"]:
        return raw

    pattern_hits = outcome.get("pattern_hits") or []
    iso_now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    write_audit(
        AuditEntry(
            user_id=user_id,
            action_name=_TOOL_RESULT_FLAGGED_ACTION,
            connector="orchestrator",
            mode="scan",
            input_summary=json.dumps(
                {
                    "tool": tool_name,
                    "pattern_hits": pattern_hits,
                    "injection_flagged": True,
                }
            ),
            result_summary=raw[:_RAW_AUDIT_MAX_CHARS],
            executed_at=datetime.now(timezone.utc),
        )
    )

    hooks.fire_background(
        Event.INJECTION_FLAGGED,
        user_id=user_id,
        source=f"tool:{tool_name}",
        file_path=f"<tool_result:{tool_name}>",
        chunk_index=None,
        severity="high",
        action="redact",
        pattern_hits=pattern_hits,
        sanitised_at=iso_now,
        stage="tool_result_middleware",
    )

    _log.warning(
        "tool_result_injection_flagged user_id=%s tool=%s patterns=%s — %s",
        user_id,
        tool_name,
        ",".join(pattern_hits),
        USER_NOTIFICATION,
    )

    return outcome["safe_text"]
