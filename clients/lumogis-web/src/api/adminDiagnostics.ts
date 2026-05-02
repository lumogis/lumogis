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
  warnings: AdminDiagnosticsWarning[];
}

/** Curated Core / store / capability / tool diagnostics (admin-only). */
export function fetchAdminDiagnostics(client: ApiClient): Promise<AdminDiagnosticsResponse> {
  return client.getJson<AdminDiagnosticsResponse>("/api/v1/admin/diagnostics");
}
