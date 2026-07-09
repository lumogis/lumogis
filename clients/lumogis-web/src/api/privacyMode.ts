// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis

import type { ApiClient } from "./client";

export type InstancePrivacyMode = "local_only" | "allow_cloud";
export type PrivacyUserRestriction = "inherit" | "local_only";

export interface InstancePrivacySettings {
  privacy_mode: InstancePrivacyMode;
  privacy_mode_locked: boolean;
  privacy_effective: InstancePrivacyMode;
}

export interface MePrivacyModeResponse {
  instance: InstancePrivacySettings;
  user_restriction: PrivacyUserRestriction;
  privacy_effective: InstancePrivacyMode;
  can_allow_cloud: boolean;
}

export interface AdminSettingsPrivacy {
  privacy_mode: InstancePrivacyMode;
  privacy_mode_locked: boolean;
  privacy_effective: InstancePrivacyMode;
}

export async function fetchMePrivacyMode(client: ApiClient): Promise<MePrivacyModeResponse> {
  return client.getJson<MePrivacyModeResponse>("/api/v1/me/privacy-mode");
}

export async function patchMePrivacyMode(
  client: ApiClient,
  body: { user_restriction: PrivacyUserRestriction },
): Promise<MePrivacyModeResponse> {
  return client.patchJson<typeof body, MePrivacyModeResponse>("/api/v1/me/privacy-mode", body);
}

export async function fetchAdminSettings(client: ApiClient): Promise<AdminSettingsPrivacy & Record<string, unknown>> {
  return client.getJson("/settings");
}

export async function putAdminPrivacySettings(
  client: ApiClient,
  body: { privacy_mode?: InstancePrivacyMode; privacy_mode_locked?: boolean },
): Promise<AdminSettingsPrivacy & Record<string, unknown>> {
  return client.putJson<typeof body, AdminSettingsPrivacy & Record<string, unknown>>("/settings", body);
}
