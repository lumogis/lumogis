// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Read-only GET /api/v1/health — non-admin per-service health (LUM-512).
// Drives graceful-degradation banners. The poll is a proactive signal only;
// the actual request/response is the source of truth for live errors.

import type { ApiClient } from "./client";

export type HealthServiceState =
  | "healthy"
  | "degraded"
  | "down"
  | "unknown"
  | "not_configured";

export type HealthOverall = "ok" | "degraded" | "down";

export interface HealthResponse {
  overall: HealthOverall;
  /** Per-service state keyed by service id (e.g. "ollama", "qdrant", "postgres"). */
  services: Record<string, HealthServiceState>;
}

export function fetchHealth(client: ApiClient): Promise<HealthResponse> {
  return client.getJson<HealthResponse>("/api/v1/health");
}
