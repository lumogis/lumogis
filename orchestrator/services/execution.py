# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Minimal :class:`ToolExecutor` and audit/permission plumbing for the Phase 3A
execution plane. The LLM still calls :func:`services.tools.run_tool`; this module
is the integration layer for future catalog-driven and capability tool paths.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from datetime import timezone
from typing import Any
from typing import Optional

from models.actions import AuditEntry
from models.tool_spec import ToolSpec
from services.capability_http import REQUIRE_BEARER_DEFAULT
from services.capability_http import HttpInvokeResult
from services.capability_http import post_capability_tool_invocation

_log = logging.getLogger(__name__)

CAPABILITY_TOOL_AUDIT_ACTION = "tool.execute.capability"

# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ToolAuditEnvelope:
    """Structured audit record for capability / executor paths.

    Phase 5: OOP executions also fan in to ``audit_log`` via
    :func:`persist_tool_audit_envelope` from the catalog bridge.

    ``status`` is one of: ok | denied | unavailable | error | forbidden_auth
    """

    user_id: str
    tool_name: str
    request_id: str | None
    capability_id: str | None
    status: str
    failure_reason: str | None = None
    result_summary: str | None = None
    connector: str | None = None
    action_type: str | None = None
    is_write: bool = False


def tool_audit_envelope_to_audit_entry(envelope: ToolAuditEnvelope) -> AuditEntry:
    """Map a tool envelope to :class:`~models.actions.AuditEntry` (no DB I/O)."""
    connector = (envelope.connector or "").strip() or (envelope.capability_id or "capability")
    mode = "DO" if envelope.is_write else "ASK"
    payload_in: dict[str, Any] = {
        "kind": "capability_tool",
        "capability_id": envelope.capability_id,
        "tool_name": envelope.tool_name,
        "request_id": envelope.request_id,
        "status": envelope.status,
        "failure_reason": envelope.failure_reason,
    }
    if envelope.action_type:
        payload_in["action_type"] = envelope.action_type
    input_summary = json.dumps(payload_in, default=str)[:500]
    fr = envelope.failure_reason
    payload_out: dict[str, Any] = {
        "audit_status": envelope.status,
        "failure_reason": (fr[:240] if fr else None),
    }
    if envelope.status == "ok" and envelope.result_summary:
        payload_out["result_preview"] = envelope.result_summary[:200]
    result_summary = json.dumps(payload_out, default=str)[:500]
    return AuditEntry(
        action_name=CAPABILITY_TOOL_AUDIT_ACTION,
        connector=connector,
        mode=mode,
        input_summary=input_summary,
        result_summary=result_summary,
        reverse_action=None,
        executed_at=datetime.now(timezone.utc),
        user_id=envelope.user_id,
    )


def persist_tool_audit_envelope(
    envelope: ToolAuditEnvelope,
    *,
    write_audit_fn: Optional[Callable[..., Optional[int]]] = None,
) -> Optional[int]:
    """Write ``envelope`` to ``audit_log`` via :func:`actions.audit.write_audit`.

    Fail-soft: never raises; unexpected mapping errors log ``oop_tool_audit.persist_failed``.
    """
    try:
        fn = write_audit_fn
        if fn is None:
            from actions.audit import write_audit as fn

        return fn(tool_audit_envelope_to_audit_entry(envelope), reverse_token=None)
    except Exception as exc:
        _log.warning(
            "oop_tool_audit.persist_failed",
            extra={"error_type": type(exc).__name__},
        )
        return None


# ---------------------------------------------------------------------------
# Permission
# ---------------------------------------------------------------------------


def _default_permission(connector: str, action_type: str, is_write: bool, user_id: str) -> bool:
    from permissions import check_permission

    return check_permission(connector, action_type, is_write, user_id=user_id)


@dataclass(frozen=True)
class PermissionCheck:
    """Thin wrapper; inject a stub in unit tests to avoid DB."""

    check: Callable[[str, str, bool, str], bool] = _default_permission

    def may_execute(
        self,
        *,
        connector: str,
        action_type: str,
        is_write: bool,
        user_id: str,
    ) -> bool:
        if not connector or not action_type or not user_id:
            return False
        return self.check(connector, action_type, is_write, user_id)


# ---------------------------------------------------------------------------
# Result + executor
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ToolExecutionResult:
    success: bool
    output: str
    denied: bool = False
    """True when :class:`PermissionCheck` failed (Ask mode, etc.)."""
    blocked_auth: bool = False
    """True when service bearer was required but missing."""


@dataclass
class ToolExecutor:
    """Execute capability HTTP tools or in-process :class:`ToolSpec` with hooks.

    Injected :attr:`emit_audit` records :class:`ToolAuditEnvelope` (tests use a
    list sink). The Phase 3B OOP bridge composes structlog plus
    :func:`persist_tool_audit_envelope` for ``audit_log`` rows.
    """

    permission: PermissionCheck
    emit_audit: Callable[[ToolAuditEnvelope], None] = lambda _e: None

    @classmethod
    def default(
        cls,
        *,
        emit_audit: Callable[[ToolAuditEnvelope], None] | None = None,
    ) -> ToolExecutor:
        return cls(permission=PermissionCheck(), emit_audit=emit_audit or (lambda _e: None))

    def execute_inprocess(
        self,
        spec: ToolSpec,
        input_: dict,
        *,
        user_id: str,
        request_id: str | None = None,
    ) -> ToolExecutionResult:
        """Run a ``ToolSpec`` handler after the standard permission check."""
        if not self.permission.may_execute(
            connector=spec.connector,
            action_type=spec.action_type,
            is_write=spec.is_write,
            user_id=user_id,
        ):
            self.emit_audit(
                ToolAuditEnvelope(
                    user_id=user_id,
                    tool_name=spec.name,
                    request_id=request_id,
                    capability_id=None,
                    status="denied",
                    failure_reason="permission check failed (connector/action)",
                    connector=spec.connector,
                    action_type=spec.action_type,
                    is_write=spec.is_write,
                )
            )
            return ToolExecutionResult(
                success=False,
                output="Permission denied",
                denied=True,
            )
        try:
            out: Any = spec.handler(input_, user_id=user_id)
        except TypeError:
            out = spec.handler(input_)
        text = out if isinstance(out, str) else str(out)
        self.emit_audit(
            ToolAuditEnvelope(
                user_id=user_id,
                tool_name=spec.name,
                request_id=request_id,
                capability_id=None,
                status="ok",
                failure_reason=None,
                result_summary=text[:500] if text else None,
                connector=spec.connector,
                action_type=spec.action_type,
                is_write=spec.is_write,
            )
        )
        return ToolExecutionResult(success=True, output=text, denied=False)

    def execute_capability_http(
        self,
        *,
        user_id: str,
        request_id: str | None,
        tool_name: str,
        capability_id: str,
        connector: str,
        action_type: str,
        is_write: bool,
        base_url: str,
        input_: dict,
        get_service_bearer: Callable[[], str | None] | None = None,
        require_service_bearer: bool = REQUIRE_BEARER_DEFAULT,
        service_healthy: bool = True,
        timeout_s: float = 2.5,
        unavailable_message: str = "capability: service unavailable",
    ) -> ToolExecutionResult:
        """POST to a capability tool endpoint; requires explicit connector/permission.

        If connector/action are unknown, pass values that do not map to a real
        permission (callers can fail earlier); a missing/empty ``connector`` is
        treated as not permitted.
        """
        if not connector.strip():
            self.emit_audit(
                ToolAuditEnvelope(
                    user_id=user_id,
                    tool_name=tool_name,
                    request_id=request_id,
                    capability_id=capability_id,
                    status="error",
                    failure_reason="missing connector (cannot permission-check)",
                    connector=connector,
                    action_type=action_type,
                    is_write=is_write,
                )
            )
            return ToolExecutionResult(
                success=False,
                output=unavailable_message,
                denied=False,
            )
        if not self.permission.may_execute(
            connector=connector,
            action_type=action_type,
            is_write=is_write,
            user_id=user_id,
        ):
            self.emit_audit(
                ToolAuditEnvelope(
                    user_id=user_id,
                    tool_name=tool_name,
                    request_id=request_id,
                    capability_id=capability_id,
                    status="denied",
                    failure_reason="permission",
                    connector=connector,
                    action_type=action_type,
                    is_write=is_write,
                )
            )
            return ToolExecutionResult(
                success=False,
                output="Permission denied",
                denied=True,
            )
        if not service_healthy:
            self.emit_audit(
                ToolAuditEnvelope(
                    user_id=user_id,
                    tool_name=tool_name,
                    request_id=request_id,
                    capability_id=capability_id,
                    status="unavailable",
                    failure_reason="service unhealthy",
                    connector=connector,
                    action_type=action_type,
                    is_write=is_write,
                )
            )
            return ToolExecutionResult(
                success=False,
                output=unavailable_message,
            )
        if require_service_bearer and get_service_bearer is None:
            self.emit_audit(
                ToolAuditEnvelope(
                    user_id=user_id,
                    tool_name=tool_name,
                    request_id=request_id,
                    capability_id=capability_id,
                    status="forbidden_auth",
                    failure_reason="get_service_bearer not configured",
                    connector=connector,
                    action_type=action_type,
                    is_write=is_write,
                )
            )
            return ToolExecutionResult(
                success=False,
                output=unavailable_message,
                blocked_auth=True,
            )
        bearer: str | None = None
        if get_service_bearer is not None:
            b = get_service_bearer()
            if isinstance(b, str) and b.strip():
                bearer = b.strip()
        if require_service_bearer and not bearer:
            self.emit_audit(
                ToolAuditEnvelope(
                    user_id=user_id,
                    tool_name=tool_name,
                    request_id=request_id,
                    capability_id=capability_id,
                    status="forbidden_auth",
                    failure_reason="missing service credential",
                    connector=connector,
                    action_type=action_type,
                    is_write=is_write,
                )
            )
            return ToolExecutionResult(
                success=False,
                output=unavailable_message,
                blocked_auth=True,
            )
        body = dict(input_)
        body["user_id"] = user_id
        res: HttpInvokeResult = post_capability_tool_invocation(
            base_url=base_url,
            tool_name=tool_name,
            user_id=user_id,
            json_body=body,
            timeout_s=timeout_s,
            service_bearer=bearer,
            require_service_bearer=require_service_bearer,
            unavailable_message=unavailable_message,
        )
        if not res.ok:
            st = "forbidden_auth" if res.error_reason == "missing_service_auth" else "error"
            self.emit_audit(
                ToolAuditEnvelope(
                    user_id=user_id,
                    tool_name=tool_name,
                    request_id=request_id,
                    capability_id=capability_id,
                    status=st,
                    failure_reason=res.error_reason,
                    connector=connector,
                    action_type=action_type,
                    is_write=is_write,
                )
            )
            if res.error_reason == "missing_service_auth":
                return ToolExecutionResult(
                    success=False,
                    output=unavailable_message,
                    blocked_auth=True,
                )
            return ToolExecutionResult(success=False, output=res.text)
        self.emit_audit(
            ToolAuditEnvelope(
                user_id=user_id,
                tool_name=tool_name,
                request_id=request_id,
                capability_id=capability_id,
                status="ok",
                failure_reason=None,
                result_summary=res.text[:500] if res.text else None,
                connector=connector,
                action_type=action_type,
                is_write=is_write,
            )
        )
        return ToolExecutionResult(success=True, output=res.text)
