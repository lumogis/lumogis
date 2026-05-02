# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Read-only aggregation for ``GET /api/v1/admin/diagnostics``.

Builds curated DTOs from existing config/adapters and the capability registry.
Does **not** run new health probes, decrypt credentials, or dump environment
values. Reuses the same store ping pattern as :func:`routes.admin.status_page`.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from datetime import datetime
from datetime import timezone

from __version__ import __version__ as CORE_VERSION
from auth import auth_enabled
from models.api_v1 import AdminDiagnosticsCapabilities
from models.api_v1 import AdminDiagnosticsCapabilityService
from models.api_v1 import AdminDiagnosticsCore
from models.api_v1 import AdminDiagnosticsResponse
from models.api_v1 import AdminDiagnosticsSpeechToText
from models.api_v1 import AdminDiagnosticsStoreItem
from models.api_v1 import AdminDiagnosticsTools
from models.api_v1 import AdminDiagnosticsWarning
from services.capability_registry import CapabilityRegistry

import config
from services import me_tools_catalog as me_tools_catalog_svc


def _store_row(name: str, check_fn: Callable[[], bool]) -> AdminDiagnosticsStoreItem:
    try:
        ok = check_fn()
        return AdminDiagnosticsStoreItem(
            name=name,
            status="ok" if ok else "unreachable",
            message=None,
        )
    except Exception:
        return AdminDiagnosticsStoreItem(name=name, status="unknown", message=None)


def _graph_store_row() -> AdminDiagnosticsStoreItem:
    gs = config.get_graph_store()
    if gs is None:
        return AdminDiagnosticsStoreItem(
            name="graph",
            status="not_configured",
            message="GRAPH_BACKEND is not falkordb",
        )
    return _store_row("graph", gs.ping)


def _mcp_flags() -> tuple[bool, bool]:
    try:
        import mcp_server as _mcp_server

        mcp_enabled = _mcp_server.mcp is not None
    except Exception:
        mcp_enabled = False
    mcp_auth_required = bool(os.environ.get("MCP_AUTH_TOKEN", "").strip())
    return mcp_enabled, mcp_auth_required


def _capabilities_block(
    registry: CapabilityRegistry,
) -> AdminDiagnosticsCapabilities:
    services = sorted(registry.all_services(), key=lambda s: s.manifest.id)
    rows: list[AdminDiagnosticsCapabilityService] = []
    for svc in services:
        st = "healthy" if svc.healthy else "unhealthy"
        rows.append(
            AdminDiagnosticsCapabilityService(
                id=svc.manifest.id,
                status=st,
                healthy=svc.healthy,
                version=svc.manifest.version,
                last_seen=svc.last_seen_healthy,
                tools=len(svc.manifest.tools),
            )
        )
    healthy_n = sum(1 for s in services if s.healthy)
    return AdminDiagnosticsCapabilities(
        total=len(services),
        healthy=healthy_n,
        unhealthy=len(services) - healthy_n,
        services=rows,
    )


def _speech_to_text_block() -> AdminDiagnosticsSpeechToText:
    """STT readiness slice — re-pings adapter when backend is not ``none``."""

    bk = config.get_stt_backend()
    max_b = config.get_stt_max_audio_bytes()
    max_d = config.get_stt_max_duration_sec()
    ep = "/api/v1/voice/transcribe"
    if bk == "none":
        return AdminDiagnosticsSpeechToText(
            backend="none",
            transcribe_available=False,
            max_audio_bytes=max_b,
            max_duration_sec=max_d,
            endpoint=ep,
        )
    adapter = config.get_speech_to_text()
    ok = False
    try:
        ok = bool(adapter and adapter.ping())
    except Exception:
        ok = False
    return AdminDiagnosticsSpeechToText(
        backend=bk,
        transcribe_available=ok,
        max_audio_bytes=max_b,
        max_duration_sec=max_d,
        endpoint=ep,
    )


def build_admin_diagnostics_response(
    admin_user_id: str,
    *,
    capability_registry: CapabilityRegistry | None = None,
    me_tools_builder: Callable[..., object] | None = None,
) -> AdminDiagnosticsResponse:
    """Assemble the wire DTO for ``GET /api/v1/admin/diagnostics``.

    ``capability_registry`` defaults to :func:`config.get_capability_registry`.
    ``me_tools_builder`` defaults to :func:`me_tools_catalog.build_me_tools_response`
    for tool summary counts (read-only catalog; no execution).
    """
    generated_at = datetime.now(timezone.utc)
    vs = config.get_vector_store()
    meta = config.get_metadata_store()
    embedder = config.get_embedder()

    stores: list[AdminDiagnosticsStoreItem] = [
        _store_row("postgres", meta.ping),
        _store_row("qdrant", vs.ping),
        _store_row("embedder", embedder.ping),
        _graph_store_row(),
    ]

    reg = (
        capability_registry if capability_registry is not None else config.get_capability_registry()
    )
    capabilities = _capabilities_block(reg)

    builder = me_tools_builder or me_tools_catalog_svc.build_me_tools_response
    me_tools = builder(admin_user_id)
    summary = me_tools.summary
    tools = AdminDiagnosticsTools(
        total=summary.total,
        available=summary.available,
        unavailable=summary.unavailable,
        by_source=dict(summary.by_source),
    )

    mcp_enabled, mcp_auth_required = _mcp_flags()
    core = AdminDiagnosticsCore(
        auth_enabled=auth_enabled(),
        tool_catalog_enabled=config.get_tool_catalog_enabled(),
        core_version=CORE_VERSION,
        mcp_enabled=mcp_enabled,
        mcp_auth_required=mcp_auth_required,
    )

    warnings: list[AdminDiagnosticsWarning] = [
        AdminDiagnosticsWarning(
            code="codegen_check_requires_live_core",
            message=(
                "npm run codegen:check compares OpenAPI to the snapshot using a "
                "running Core endpoint (LUMOGIS_OPENAPI_URL); offline CI may skip it."
            ),
        ),
    ]

    critical_ok = stores[0].status == "ok"  # postgres
    others_ok = all(s.status in ("ok", "not_configured") for s in stores[1:])
    overall = "ok" if critical_ok and others_ok else "degraded"

    return AdminDiagnosticsResponse(
        status=overall,
        generated_at=generated_at,
        core=core,
        stores=stores,
        capabilities=capabilities,
        tools=tools,
        warnings=warnings,
        speech_to_text=_speech_to_text_block(),
    )
