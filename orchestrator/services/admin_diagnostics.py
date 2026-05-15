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
from models.api_v1 import AdminDiagnosticsFoundationCapabilityRegistry
from models.api_v1 import AdminDiagnosticsFoundationPermissions
from models.api_v1 import AdminDiagnosticsFoundationSignals
from models.api_v1 import AdminDiagnosticsFoundationToolCatalog
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


def _sorted_count_map(counts: dict[str, int]) -> dict[str, int]:
    return dict(sorted(counts.items()))


def _permissions_module_probe(admin_user_id: str) -> tuple[bool, bool]:
    """Return ``(import_ok, connector_mode_lookup_ok)`` for Ask/Do sanity."""
    try:
        import permissions as perm_module
    except Exception:
        return False, False
    try:
        mode = perm_module.get_connector_mode(
            user_id=admin_user_id,
            connector="__lumogis_admin_diag_probe__",
        )
    except Exception:
        return True, False
    if isinstance(mode, str) and mode.strip().upper() in ("ASK", "DO"):
        return True, True
    return True, False


def _build_foundation_signals(
    *,
    admin_user_id: str,
    me_tools: object,
    capabilities: AdminDiagnosticsCapabilities,
) -> AdminDiagnosticsFoundationSignals:
    """ADR 034 read layer — derives from catalog façade rows already built for diagnostics."""
    items = getattr(me_tools, "tools", None) or []

    entries_by_transport: dict[str, int] = {}
    unavailable_by_source: dict[str, int] = {}
    unavailable_capability_catalog_entries = 0
    catalog_only_transport_entries = 0

    for row in items:
        transport = row.transport
        entries_by_transport[transport] = entries_by_transport.get(transport, 0) + 1
        source = row.source
        if not row.available:
            unavailable_by_source[source] = unavailable_by_source.get(source, 0) + 1
            if source == "capability":
                unavailable_capability_catalog_entries += 1
        if transport == "catalog_only":
            catalog_only_transport_entries += 1

    import_ok, lookup_ok = _permissions_module_probe(admin_user_id)
    connector_perm_unknown = 0
    for row in items:
        conn = row.connector
        if conn and str(conn).strip() and row.permission_mode == "unknown":
            connector_perm_unknown += 1

    total_entries = len(items)
    summary = getattr(me_tools, "summary", None)
    if total_entries == 0 and summary is not None:
        total_entries = int(summary.total)

    return AdminDiagnosticsFoundationSignals(
        tool_catalog=AdminDiagnosticsFoundationToolCatalog(
            total_entries=total_entries,
            entries_by_transport=_sorted_count_map(entries_by_transport),
            unavailable_entries_by_source=_sorted_count_map(unavailable_by_source),
            unavailable_capability_catalog_entries=unavailable_capability_catalog_entries,
            catalog_only_transport_entries=catalog_only_transport_entries,
        ),
        permissions=AdminDiagnosticsFoundationPermissions(
            ask_do_module_import_ok=import_ok,
            connector_mode_metadata_lookup_ok=lookup_ok,
            catalog_rows_with_connector_but_unknown_permission_mode=connector_perm_unknown,
        ),
        capability_registry=AdminDiagnosticsFoundationCapabilityRegistry(
            registered_services_total=capabilities.total,
            registered_services_unhealthy=capabilities.unhealthy,
        ),
    )


def _foundation_extra_warnings(
    sig: AdminDiagnosticsFoundationSignals,
) -> list[AdminDiagnosticsWarning]:
    out: list[AdminDiagnosticsWarning] = []
    if not sig.permissions.ask_do_module_import_ok:
        out.append(
            AdminDiagnosticsWarning(
                code="permissions_module_import_failed",
                message="Could not import the permissions module; Ask/Do sanity check skipped.",
            )
        )
    elif not sig.permissions.connector_mode_metadata_lookup_ok:
        out.append(
            AdminDiagnosticsWarning(
                code="connector_mode_lookup_failed",
                message=(
                    "Connector mode metadata probe failed against Postgres "
                    "(see stores.postgres status); Ask/Do read model may be stale."
                ),
            )
        )
    uh = sig.capability_registry.registered_services_unhealthy
    if uh > 0:
        out.append(
            AdminDiagnosticsWarning(
                code="capability_services_unhealthy",
                message=(
                    f"{uh} registered capability service(s) are unhealthy — "
                    "capability-backed catalog rows may show as unavailable."
                ),
            )
        )
    n = sig.permissions.catalog_rows_with_connector_but_unknown_permission_mode
    if n > 0:
        out.append(
            AdminDiagnosticsWarning(
                code="tool_catalog_permission_mode_unknown",
                message=(
                    f"{n} catalog row(s) have a connector id but permission_mode stayed unknown "
                    "(check connector_permissions or orchestrator logs)."
                ),
            )
        )
    return out


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

    foundation_signals = _build_foundation_signals(
        admin_user_id=admin_user_id,
        me_tools=me_tools,
        capabilities=capabilities,
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
    warnings.extend(_foundation_extra_warnings(foundation_signals))

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
        foundation_signals=foundation_signals,
        warnings=warnings,
        speech_to_text=_speech_to_text_block(),
    )
