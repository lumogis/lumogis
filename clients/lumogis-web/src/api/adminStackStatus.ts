// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Read-only GET /api/v1/admin/diagnostics/stack-status.

import type { ApiClient } from "./client";

export type StackServiceState =
  | "healthy"
  | "degraded"
  | "down"
  | "unknown"
  | "not_configured";

export type StackOverallStatus = "ok" | "degraded" | "down";

export interface StackStatusServiceItem {
  id: string;
  display_name: string;
  state: StackServiceState;
  runtime_kind: "docker_compose" | "process" | "unknown";
  runtime_detail: Record<string, string | number | null>;
  message: string | null;
}

export type StackStorageStatus = "ok" | "warn" | "critical" | "unknown";

export interface StackStatusStorageItem {
  mount_id: string;
  path_label: string;
  partition_id: string | null;
  used_bytes: number | null;
  total_bytes: number | null;
  used_percent: number | null;
  warn_threshold_percent: number;
  status: StackStorageStatus;
}

export interface StackStatusOllamaModel {
  name: string;
  size_bytes: number | null;
  modified_at: string | null;
  loaded: boolean | null;
}

export interface StackStatusWarning {
  code: string;
  message: string;
}

export interface StackStatusMeta {
  generated_at: string;
  cache_age_sec: number | null;
  stack_control_reachable: boolean;
  overall_status: StackOverallStatus;
}

export interface StackStatusResponse {
  meta: StackStatusMeta;
  services: StackStatusServiceItem[];
  storage: StackStatusStorageItem[];
  ollama: StackStatusOllamaModel[];
  warnings: StackStatusWarning[];
}

export function fetchAdminStackStatus(client: ApiClient): Promise<StackStatusResponse> {
  return client.getJson<StackStatusResponse>("/api/v1/admin/diagnostics/stack-status");
}
