// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Read-only GET /api/v1/admin/diagnostics. Admin-only; no secrets.

import type { ApiClient } from "./client";
import type { components } from "./generated/openapi";

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
  /** The configured `STT_MODEL` (whisper_sidecar only). */
  model?: string | null;
  /** Whether the engine has the speech model; typed from the generated schema. */
  model_state?: components["schemas"]["AdminDiagnosticsSpeechToText"]["model_state"];
}

export interface AdminDiagnosticsWarning {
  code: string;
  message: string;
}

/** Why a configured model of a background task would be skipped (LUM-877). */
export type AdminDiagnosticsTaskSkipReason =
  | "unknown"
  | "not_on_box"
  | "no_tools"
  | "no_credentials"
  | "optional_disabled"
  | "privacy_mode"
  | "error";

export interface AdminDiagnosticsTaskEntry {
  model: string;
  /** null means this entry is usable now. */
  skip_reason: AdminDiagnosticsTaskSkipReason | null;
}

/** A background model task (LUM-877): its rules, its list, the model it resolves to for you. */
export interface AdminDiagnosticsTask {
  task: string;
  description: string;
  local_only: boolean;
  needs_tools: boolean;
  /** false means some runs of this task have no user. */
  threads_user_id: boolean;
  /** true means only the first entry is ever used. */
  destructive: boolean;
  configured: AdminDiagnosticsTaskEntry[];
  default_model: string;
  /** null means the job skips the LLM (on-box-only task, nothing on-box). */
  resolved_model: string | null;
  resolved_from: "list" | "default";
  /** Why the code default will fail downstream; null/absent means it is usable. */
  default_skip_reason?: AdminDiagnosticsTaskSkipReason | null;
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
  /** Background model tasks (LUM-877). Absent on older Core builds. */
  tasks?: AdminDiagnosticsTask[];
}

/** Curated Core / store / capability / tool diagnostics (admin-only). */
export function fetchAdminDiagnostics(client: ApiClient): Promise<AdminDiagnosticsResponse> {
  return client.getJson<AdminDiagnosticsResponse>("/api/v1/admin/diagnostics");
}
