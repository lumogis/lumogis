// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Read-only façade for GET /api/v1/me/notifications. No secrets.

import type { ApiClient } from "./client";

export type MeNotificationActiveTier = "user" | "household" | "system" | "env" | "none";

export interface MeNotificationChannelItem {
  connector: string;
  label: string;
  description: string;
  configured: boolean;
  active_tier: MeNotificationActiveTier;
  user_credential_present: boolean;
  household_credential_available: boolean;
  system_credential_available: boolean;
  env_fallback_available: boolean;
  url: string | null;
  url_configured: boolean | null;
  topic_configured: boolean | null;
  token_configured: boolean | null;
  updated_at: string | null;
  key_version: number | null;
  subscription_count: number | null;
  push_service_configured: boolean | null;
  status: "configured" | "not_configured";
  why_not_available: string | null;
}

export interface MeNotificationsSummary {
  total: number;
  configured: number;
  not_configured: number;
  by_active_tier: Record<string, number>;
}

export interface MeNotificationsResponse {
  channels: MeNotificationChannelItem[];
  summary: MeNotificationsSummary;
}

/** Fetch curated notification channel status (read-only). */
export function fetchMeNotifications(client: ApiClient): Promise<MeNotificationsResponse> {
  return client.getJson<MeNotificationsResponse>("/api/v1/me/notifications");
}
