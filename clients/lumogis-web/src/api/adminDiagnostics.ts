// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Read-only GET /api/v1/admin/diagnostics. Admin-only; no secrets.

import type { ApiClient } from "./client";

export type AdminDiagnosticsOverallStatus = "ok" | "degraded";

export interface AdminDiagnosticsCore {
  auth_enabled: boolean;
  tool_catalog_enabled: boolean;
  core_version: string;
  mcp_enabled: boolean;
  mcp_auth_required: boolean;
}

export type AdminStoreStatus = "ok" | "unreachable" | "unknown" | "not_configured";

export interface AdminDiagnosticsStoreItem {
  name: string;
  status: AdminStoreStatus;
  message: string | null;
}

export interface AdminDiagnosticsCapabilityService {
  id: string;
  status: "healthy" | "unhealthy";
  healthy: boolean;
  version: string;
  last_seen: string | null;
  tools: number;
}

export interface AdminDiagnosticsCapabilities {
  total: number;
  healthy: number;
  unhealthy: number;
  services: AdminDiagnosticsCapabilityService[];
}

export interface AdminDiagnosticsTools {
  total: number;
  available: number;
  unavailable: number;
  by_source: Record<string, number>;
}

/** ADR 034 — Agent Harness Foundation; read-only operator signals */
export interface AdminDiagnosticsFoundationToolCatalog {
  total_entries: number;
  entries_by_transport: Record<string, number>;
  unavailable_entries_by_source: Record<string, number>;
  unavailable_capability_catalog_entries: number;
  catalog_only_transport_entries: number;
}

export interface AdminDiagnosticsFoundationPermissions {
  ask_do_module_import_ok: boolean;
  connector_mode_metadata_lookup_ok: boolean;
  catalog_rows_with_connector_but_unknown_permission_mode: number;
}

export interface AdminDiagnosticsFoundationCapabilityRegistry {
  registered_services_total: number;
  registered_services_unhealthy: number;
}

export interface AdminDiagnosticsFoundationSignals {
  tool_catalog: AdminDiagnosticsFoundationToolCatalog;
  permissions: AdminDiagnosticsFoundationPermissions;
  capability_registry: AdminDiagnosticsFoundationCapabilityRegistry;
}

export interface AdminDiagnosticsSpeechToText {
  backend: "none" | "fake_stt" | "whisper_sidecar";
  transcribe_available: boolean;
  max_audio_bytes: number;
  max_duration_sec: number;
  endpoint: string;
}

export interface AdminDiagnosticsWarning {
  code: string;
  message: string;
}

export interface AdminDiagnosticsResponse {
  status: AdminDiagnosticsOverallStatus;
  generated_at: string;
  core: AdminDiagnosticsCore;
  stores: AdminDiagnosticsStoreItem[];
  capabilities: AdminDiagnosticsCapabilities;
  tools: AdminDiagnosticsTools;
  /** Present on current Core; omit on older builds. */
  foundation_signals?: AdminDiagnosticsFoundationSignals;
  warnings: AdminDiagnosticsWarning[];
  speech_to_text: AdminDiagnosticsSpeechToText;
}

/** Curated Core / store / capability / tool diagnostics (admin-only). */
export function fetchAdminDiagnostics(client: ApiClient): Promise<AdminDiagnosticsResponse> {
  return client.getJson<AdminDiagnosticsResponse>("/api/v1/admin/diagnostics");
}
