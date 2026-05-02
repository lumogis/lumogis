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

import structlog
from models.actions import AuditEntry

import config

_log = logging.getLogger(__name__)


def _audit_logger():
    """Return the stdlib-bridged structlog logger for audit events.

    Resolved per call (NOT cached at module import) so that
    ``structlog.testing.capture_logs()`` in tests can intercept
    ``audit.executed`` / ``audit.write_failed`` events that fire from
    inside the ``with capture_logs()`` block. The dedicated logger
    name ``lumogis.audit`` lets log-aggregation rules pin on the
    audit-mirror channel without scraping the module path. The mirror
    NEVER emits payload bodies (input_summary / result_summary /
    reverse_token / reverse_action) — those stay in the Postgres
    audit_log row, cross-referenced via ``audit_id``.
    """
    return structlog.get_logger("lumogis.audit")


def write_audit(entry: AuditEntry, reverse_token: Optional[str] = None) -> Optional[int]:
    """Insert an AuditEntry into audit_log. Returns the row id, or None on failure.

    On success, mirrors a single ``audit.executed`` structured event to
    stdout with cross-reference fields (``audit_id``, ``user_id``,
    ``action_name``, ``connector``, ``mode``, ``is_reversible``). Payload
    bodies stay in the DB row.
    """
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
        audit_id = row["id"] if row else None
        if audit_id is not None:
            _audit_logger().info(
                "audit.executed",
                audit_id=audit_id,
                user_id=entry.user_id,
                action_name=entry.action_name,
                connector=entry.connector,
                mode=entry.mode,
                is_reversible=bool(reverse_token),
            )
        return audit_id
    except Exception as exc:
        # NEVER include the AuditEntry payload (input_summary /
        # result_summary / reverse_action) in the failure event — the
        # whole reason the row failed to land may be that one of those
        # fields was malformed, and the redaction processor cannot reason
        # about every connector's payload shape. Keep stdout minimal;
        # the operator who needs more re-runs with DEBUG logging.
        _audit_logger().error(
            "audit.write_failed",
            error=type(exc).__name__,
            message=str(exc),
        )
        return None


def get_audit(
    connector: Optional[str] = None,
    action_type: Optional[str] = None,
    *,
    user_id: str,
    limit: int = 50,
) -> list[dict]:
    """Fetch recent audit log entries. Filter by connector or action_type.

    Phase 3: ``user_id`` is keyword-only and required. The audit log is
    a per-user record; never bulk-return everyone's actions because a
    caller forgot to scope.
    """
    if not isinstance(user_id, str) or not user_id:
        raise TypeError("get_audit: user_id (keyword-only) is required")
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
