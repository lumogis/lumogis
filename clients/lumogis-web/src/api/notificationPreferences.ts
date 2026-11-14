// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// GET/PATCH /api/v1/me/notification-preferences — routing prefs matrix.

import type { ApiClient } from "./client";

export type ChannelId = "ntfy" | "web_push" | "in_app";

export type NotificationType =
  | "routine_elevation"
  | "signal_received"
  | "signal_digest"
  | "action_executed"
  | "security_alert"
  | "consolidation_done"
  // LUM-648: appear in GET /api/v1/me/notification-preferences immediately
  // (the backend iterates every NotificationType).
  | "signal_needs_you"
  | "signal_morning_brief";

export interface NotificationPreferenceCell {
  channel: ChannelId;
  enabled: boolean;
  effective: boolean;
  mutable: boolean;
  tier_default: boolean;
}

export interface NotificationTypePrefsRow {
  notification_type: NotificationType;
  tier: string;
  channels: NotificationPreferenceCell[];
}

export interface NotificationPreferencesResponse {
  types: NotificationTypePrefsRow[];
  timezone: string | null;
  quiet_hours_start: string | null;
  quiet_hours_end: string | null;
}

export interface NotificationPreferencePatchItem {
  notification_type: NotificationType;
  channel: ChannelId;
  enabled: boolean;
}

export interface NotificationPreferencesPatch {
  // Optional: settings-only patches (quiet hours / timezone, no channel
  // toggles) are valid — omit `preferences` or pass `[]`.
  preferences?: NotificationPreferencePatchItem[];
  // Omit a field to leave it unchanged; pass `null` to clear it.
  timezone?: string | null;
  quiet_hours_start?: string | null;
  quiet_hours_end?: string | null;
}

export function fetchNotificationPreferences(
  client: ApiClient,
): Promise<NotificationPreferencesResponse> {
  return client.getJson<NotificationPreferencesResponse>("/api/v1/me/notification-preferences");
}

export function patchNotificationPreference(
  client: ApiClient,
  body: NotificationPreferencesPatch,
): Promise<NotificationPreferencesResponse> {
  return client.patchJson<NotificationPreferencesPatch, NotificationPreferencesResponse>(
    "/api/v1/me/notification-preferences",
    body,
  );
}
