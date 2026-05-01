# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Audit log — append-only record of action executions.

audit_log is distinct from action_log:
  action_log  — permission checks for tool calls via services/tools.py
  audit_log   — action execution and reversibility via actions/executor.py

No UPDATE or DELETE methods are provided intentionally.
"""

import json
import logging
from datetime import datetime
from datetime import timezone
from typing import Optional

from models.actions import AuditEntry

import config

_log = logging.getLogger(__name__)


def write_audit(entry: AuditEntry, reverse_token: Optional[str] = None) -> Optional[int]:
    """Insert an AuditEntry into audit_log. Returns the row id, or None on failure."""
    try:
        ms = config.get_metadata_store()
        row = ms.fetch_one(
            "INSERT INTO audit_log "
            "(user_id, action_name, connector, mode, input_summary, result_summary, "
            "reverse_token, reverse_action, executed_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s) "
            "RETURNING id",
            (
                entry.user_id,
                entry.action_name,
                entry.connector,
                entry.mode,
                entry.input_summary,
                entry.result_summary,
                reverse_token,
                json.dumps(entry.reverse_action) if entry.reverse_action else None,
                entry.executed_at or datetime.now(timezone.utc),
            ),
        )
        return row["id"] if row else None
    except Exception as exc:
        _log.error("audit write error: %s", exc)
        return None


def get_audit(
    connector: Optional[str] = None,
    action_type: Optional[str] = None,
    user_id: str = "default",
    limit: int = 50,
) -> list[dict]:
    """Fetch recent audit log entries. Filter by connector or action_type."""
    conditions = ["user_id = %s"]
    params: list = [user_id]

    if connector:
        conditions.append("connector = %s")
        params.append(connector)
    if action_type:
        conditions.append("action_name LIKE %s")
        params.append(f"%{action_type}%")

    params.append(limit)
    where = " AND ".join(conditions)

    try:
        ms = config.get_metadata_store()
        return ms.fetch_all(
            f"SELECT id, action_name, connector, mode, input_summary, result_summary, "
            f"reverse_token, reverse_action, executed_at, reversed_at "
            f"FROM audit_log WHERE {where} ORDER BY executed_at DESC LIMIT %s",
            tuple(params),
        )
    except Exception as exc:
        _log.error("audit fetch error: %s", exc)
        return []


def mark_reversed(reverse_token: str) -> bool:
    """Mark an audit entry as reversed (sets reversed_at timestamp)."""
    try:
        ms = config.get_metadata_store()
        ms.execute(
            "UPDATE audit_log SET reversed_at = NOW() WHERE reverse_token = %s",
            (reverse_token,),
        )
        return True
    except Exception as exc:
        _log.error("mark_reversed error: %s", exc)
        return False
