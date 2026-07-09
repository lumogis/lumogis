# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Derived event_type taxonomy for audit_log rows (LUM-197).

v1 maps existing ``action_name`` + ``connector`` to stable namespaced
``event_type`` keys. Filtering uses reverse predicates over SQL columns
only — never ``input_summary``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

USER_INVITE_MINTED = "__user_invite__.minted"
USER_INVITE_REDEEMED = "__user_invite__.redeemed"
USER_INVITE_REVOKED = "__user_invite__.revoked"

ACTION_MINTED = "__mcp_token__.minted"
ACTION_REVOKED = "__mcp_token__.revoked"
ACTION_ADMIN_REVOKED = "__mcp_token__.admin_revoked"
ACTION_CASCADE_REVOKED = "__mcp_token__.cascade_revoked"

ACTION_SESSION_MINTED = "__session__.minted"
ACTION_SESSION_REVOKED = "__session__.revoked"
ACTION_SESSION_ADMIN_REVOKED = "__session__.admin_revoked"
ACTION_SESSION_CASCADE_REVOKED = "__session__.cascade_revoked"
ACTION_SESSION_REUSE_DETECTED = "__session__.reuse_detected"

ACTION_CRED_PUT = "__connector_credential__.put"
ACTION_CRED_DELETED = "__connector_credential__.deleted"
ACTION_CRED_ROTATED = "__connector_credential__.rotated"

USER_EXPORT_STARTED = "__user_export__.started"
USER_EXPORT_FAILED = "__user_export__.failed"
USER_EXPORT_PRUNE_FAILED = "__user_export__.prune_failed"
USER_EXPORT_COMPLETED = "__user_export__.completed"

USER_IMPORT_REFUSED = "__user_import__.refused"
USER_IMPORT_DRY_RUN_REQUESTED = "__user_import__.dry_run_requested"
USER_IMPORT_DRY_RUN_VALIDATION_PASSED = "__user_import__.dry_run_validation_passed"
USER_IMPORT_DRY_RUN_VALIDATION_FAILED = "__user_import__.dry_run_validation_failed"
USER_IMPORT_STARTED = "__user_import__.started"
USER_IMPORT_FAILED = "__user_import__.failed"
USER_IMPORT_COMPLETED = "__user_import__.completed"

PRIVACY_MODE_BLOCK = "privacy_mode_block"
CAPTURE_INDEX = "capture_index"
TOOL_EXECUTE_CAPABILITY = "tool.execute.capability"
PERMISSIONS_CHANGE_CONNECTOR = "__permissions_change__"

USER_INVITE_ACTIONS = (
    USER_INVITE_MINTED,
    USER_INVITE_REDEEMED,
    USER_INVITE_REVOKED,
)
MCP_TOKEN_ACTIONS = (
    ACTION_MINTED,
    ACTION_REVOKED,
    ACTION_ADMIN_REVOKED,
    ACTION_CASCADE_REVOKED,
)
SESSION_ACTIONS = (
    ACTION_SESSION_MINTED,
    ACTION_SESSION_REVOKED,
    ACTION_SESSION_ADMIN_REVOKED,
    ACTION_SESSION_CASCADE_REVOKED,
    ACTION_SESSION_REUSE_DETECTED,
)
CREDENTIAL_ACTIONS = (
    ACTION_CRED_PUT,
    ACTION_CRED_DELETED,
    ACTION_CRED_ROTATED,
)
EXPORT_ACTIONS = (
    USER_EXPORT_STARTED,
    USER_EXPORT_FAILED,
    USER_EXPORT_PRUNE_FAILED,
    USER_EXPORT_COMPLETED,
)
IMPORT_ACTIONS = (
    USER_IMPORT_REFUSED,
    USER_IMPORT_DRY_RUN_REQUESTED,
    USER_IMPORT_DRY_RUN_VALIDATION_PASSED,
    USER_IMPORT_DRY_RUN_VALIDATION_FAILED,
    USER_IMPORT_STARTED,
    USER_IMPORT_FAILED,
    USER_IMPORT_COMPLETED,
)

EXACT_MAPPED_ACTION_NAMES: tuple[str, ...] = (
    PRIVACY_MODE_BLOCK,
    CAPTURE_INDEX,
    TOOL_EXECUTE_CAPABILITY,
    *USER_INVITE_ACTIONS,
    *MCP_TOKEN_ACTIONS,
    *SESSION_ACTIONS,
    *CREDENTIAL_ACTIONS,
    *EXPORT_ACTIONS,
    *IMPORT_ACTIONS,
)

# Escaped LIKE patterns for namespace prefixes (exclude_mapped SQL).
PREFIX_EXCLUSION_PATTERNS: tuple[str, ...] = (
    r"\_\_user\_invite\_\_.%",
    r"\_\_mcp\_token\_\_.%",
    r"\_\_session\_\_.%",
    r"\_\_connector\_credential\_\_.%",
    r"\_\_user\_export\_\_.%",
    r"\_\_user\_import\_\_.%",
)

_NAMESPACE_PREFIXES: tuple[tuple[str, str], ...] = (
    ("__user_invite__.", "auth.invite."),
    ("__mcp_token__.", "auth.mcp_token."),
    ("__session__.", "auth.session."),
    ("__connector_credential__.", "auth.credential."),
    ("__user_export__.", "data.export."),
    ("__user_import__.", "data.import."),
)

_SECRET_KEYS = frozenset(
    {
        "password",
        "secret",
        "token",
        "api_key",
        "apikey",
        "bearer",
        "reverse_token",
        "credential",
        "private_key",
    }
)


@dataclass(frozen=True)
class AuditFilterPredicate:
    action_names: tuple[str, ...] = ()
    connector: str | None = None
    exclude_mapped: bool = False
    routine_prefix_only: bool = False


def derive_event_type(
    action_name: str,
    connector: str,
    input_summary: str | None = None,
) -> str:
    """Map a stored audit row to a namespaced event_type key."""
    del input_summary  # v1 filter contract: never branch on input_summary
    if not action_name:
        return "audit.unknown"

    if action_name == PRIVACY_MODE_BLOCK:
        return "privacy.external_call.denied"
    if action_name == CAPTURE_INDEX:
        return "data.capture.indexed"
    if action_name == TOOL_EXECUTE_CAPABILITY:
        return "action.executed"
    if action_name.startswith("routine:"):
        return "action.routine.executed"

    for prefix, event_prefix in _NAMESPACE_PREFIXES:
        if action_name.startswith(prefix):
            verb = action_name[len(prefix) :]
            if verb:
                return f"{event_prefix}{verb}"

    if action_name.startswith("__permissions_change__."):
        return "auth.permissions.changed"

    return "action.executed"


def derive_source(connector: str, mode: str) -> str:
    if mode:
        return f"{connector}/{mode}"
    return connector


def is_privacy_or_cloud_row(event_type: str, action_name: str) -> bool:
    return event_type.startswith("privacy.") or action_name == PRIVACY_MODE_BLOCK



def _all_exact_event_types() -> dict[str, AuditFilterPredicate]:
    mapping: dict[str, AuditFilterPredicate] = {
        "privacy.external_call.denied": AuditFilterPredicate(action_names=(PRIVACY_MODE_BLOCK,)),
        "data.capture.indexed": AuditFilterPredicate(action_names=(CAPTURE_INDEX,)),
        "auth.permissions.changed": AuditFilterPredicate(connector=PERMISSIONS_CHANGE_CONNECTOR),
        "action.routine.executed": AuditFilterPredicate(routine_prefix_only=True),
        "action.executed": AuditFilterPredicate(exclude_mapped=True),
    }
    for prefix, event_prefix in _NAMESPACE_PREFIXES:
        actions: tuple[str, ...]
        if prefix == "__user_invite__.":
            actions = USER_INVITE_ACTIONS
        elif prefix == "__mcp_token__.":
            actions = MCP_TOKEN_ACTIONS
        elif prefix == "__session__.":
            actions = SESSION_ACTIONS
        elif prefix == "__connector_credential__.":
            actions = CREDENTIAL_ACTIONS
        elif prefix == "__user_export__.":
            actions = EXPORT_ACTIONS
        elif prefix == "__user_import__.":
            actions = IMPORT_ACTIONS
        else:
            continue
        for action in actions:
            verb = action[len(prefix) :]
            mapping[f"{event_prefix}{verb}"] = AuditFilterPredicate(action_names=(action,))
    return mapping


_EXACT_EVENT_TYPE_PREDICATES = _all_exact_event_types()


def action_names_for_event_type(event_type: str) -> AuditFilterPredicate | None:
    """Reverse lookup for SQL-side event_type filtering."""
    if not event_type:
        return None

    exact = _EXACT_EVENT_TYPE_PREDICATES.get(event_type)
    if exact is not None:
        return exact

    # Category prefix: ≤2 dot segments (e.g. auth.invite, auth, data.export).
    if event_type.count(".") > 1:
        return None

    child_prefix = f"{event_type}."
    matched_names: list[str] = []
    matched_routine = False
    matched_connector: str | None = None

    for key, pred in _EXACT_EVENT_TYPE_PREDICATES.items():
        if key == "action.executed":
            continue
        if not key.startswith(child_prefix):
            continue
        if pred.routine_prefix_only:
            matched_routine = True
        elif pred.connector and not pred.action_names:
            matched_connector = pred.connector
        else:
            matched_names.extend(pred.action_names)

    if matched_routine and not matched_names and not matched_connector:
        return AuditFilterPredicate(routine_prefix_only=True)
    if matched_connector and not matched_names:
        return AuditFilterPredicate(connector=matched_connector)
    if matched_names:
        return AuditFilterPredicate(action_names=tuple(dict.fromkeys(matched_names)))
    return None


def apply_predicate_to_sql(
    predicate: AuditFilterPredicate,
    conditions: list[str],
    params: list[Any],
) -> None:
    """Append parameterised SQL fragments for an AuditFilterPredicate."""
    if predicate.routine_prefix_only:
        conditions.append("action_name LIKE 'routine:%' ESCAPE '\\'")
        return

    if predicate.action_names:
        placeholders = ", ".join(["%s"] * len(predicate.action_names))
        conditions.append(f"action_name IN ({placeholders})")
        params.extend(predicate.action_names)

    if predicate.connector is not None:
        conditions.append("connector = %s")
        params.append(predicate.connector)

    if predicate.exclude_mapped:
        if EXACT_MAPPED_ACTION_NAMES:
            placeholders = ", ".join(["%s"] * len(EXACT_MAPPED_ACTION_NAMES))
            conditions.append(f"action_name NOT IN ({placeholders})")
            params.extend(EXACT_MAPPED_ACTION_NAMES)
        conditions.append("action_name NOT LIKE 'routine:%' ESCAPE '\\'")
        conditions.append("connector != %s")
        params.append(PERMISSIONS_CHANGE_CONNECTOR)
        for pattern in PREFIX_EXCLUSION_PATTERNS:
            conditions.append("NOT (action_name LIKE %s ESCAPE '\\')")
            params.append(pattern)


def build_description(row: dict[str, Any]) -> str:
    """Human-readable one-liner from audit row summaries."""
    parts: list[str] = []

    for field in ("input_summary", "result_summary"):
        raw = row.get(field)
        if not raw:
            continue
        parsed = _try_parse_json(raw)
        if parsed:
            for key in ("requested_model", "invite_id", "connector", "decline_type"):
                if key in parsed and parsed[key] is not None:
                    val = str(parsed[key])
                    if not _looks_secret(key, val):
                        parts.append(f"{key}={val}")
        else:
            snippet = _truncate(raw)
            if snippet and not _contains_secret(snippet):
                parts.append(snippet)

    if parts:
        return _truncate("; ".join(parts))
    action = row.get("action_name") or "action"
    connector = row.get("connector") or ""
    return _truncate(f"{action} via {connector}".strip())


def enrich_audit_row(row: dict[str, Any]) -> dict[str, Any]:
    """Add derived DTO fields to a raw audit_log row dict."""
    action_name = row.get("action_name") or ""
    connector = row.get("connector") or ""
    mode = row.get("mode") or ""
    input_summary = row.get("input_summary")
    event_type = derive_event_type(action_name, connector, input_summary)
    enriched = dict(row)
    enriched["event_type"] = event_type
    enriched["scope"] = row.get("scope") or "personal"
    enriched["source"] = derive_source(connector, mode)
    enriched["description"] = build_description(row)
    return enriched


def _try_parse_json(raw: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _truncate(text: str, max_len: int = 200) -> str:
    text = text.strip()
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "…"


def _looks_secret(key: str, value: str) -> bool:
    key_lower = key.lower()
    if key_lower in _SECRET_KEYS or any(s in key_lower for s in _SECRET_KEYS):
        return True
    return _contains_secret(value)


def _contains_secret(text: str) -> bool:
    lower = text.lower()
    if "bearer " in lower:
        return True
    if re.search(r"lin_api_[a-z0-9]+", lower):
        return True
    if "reverse_token" in lower:
        return True
    for key in _SECRET_KEYS:
        if key in lower and "=" in text:
            return True
    return False
