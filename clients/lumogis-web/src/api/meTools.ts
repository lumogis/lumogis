// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Read-only façade for GET /api/v1/me/tools (orchestrator/models/api_v1.py).
// Observational catalog only — no execution.

import type { ApiClient } from "./client";

export interface MeToolsSummary {
  total: number;
  available: number;
  unavailable: number;
  by_source: Record<string, number>;
}

export interface MeToolsItem {
  name: string;
  label: string;
  description: string;
  source: string;
  transport: string;
  origin_tier: string;
  available: boolean;
  why_not_available: string | null;
  capability_id: string | null;
  connector: string | null;
  action_type: string | null;
  permission_mode: string;
  requires_credentials: boolean;
}

export interface MeToolsResponse {
  tools: MeToolsItem[];
  summary: MeToolsSummary;
}

/** Fetch the unified tool catalog snapshot for the current user (read-only). */
export function fetchMeTools(client: ApiClient): Promise<MeToolsResponse> {
  return client.getJson<MeToolsResponse>("/api/v1/me/tools");
}
