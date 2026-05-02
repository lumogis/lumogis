// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Read-only façade for GET /api/v1/me/llm-providers. No secrets.

import type { ApiClient } from "./client";

export type MeLlmActiveTier = "user" | "household" | "system" | "env" | "none";

export interface MeLlmProviderItem {
  connector: string;
  label: string;
  description: string;
  configured: boolean;
  active_tier: MeLlmActiveTier;
  user_credential_present: boolean;
  household_credential_available: boolean;
  system_credential_available: boolean;
  env_fallback_available: boolean;
  updated_at: string | null;
  key_version: number | null;
  status: "configured" | "not_configured";
  why_not_available: string | null;
}

export interface MeLlmProvidersSummary {
  total: number;
  configured: number;
  not_configured: number;
  by_active_tier: Record<string, number>;
}

export interface MeLlmProvidersResponse {
  providers: MeLlmProviderItem[];
  summary: MeLlmProvidersSummary;
}

/** Fetch curated LLM provider credential status for the current user (read-only). */
export function fetchMeLlmProviders(client: ApiClient): Promise<MeLlmProvidersResponse> {
  return client.getJson<MeLlmProvidersResponse>("/api/v1/me/llm-providers");
}
