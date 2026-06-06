// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Typed façade for GET/PATCH /api/v1/me/wow-state (LUM-216).

import type { ApiClient } from "./client";

export interface WowTopEntity {
  entity_id: string;
  name: string;
  entity_type: string;
  mention_count: number;
  scope: "personal" | "shared" | "system";
}

export interface MeWowStateResponse {
  entities_ready: boolean;
  top_entities: WowTopEntity[];
  wow_dismissed_at: string | null;
  onboarding_completed_at: string | null;
}

export interface MeWowPatchBody {
  dismissed: true;
}

export function getMeWowState(client: ApiClient): Promise<MeWowStateResponse> {
  return client.getJson<MeWowStateResponse>("/api/v1/me/wow-state");
}

export function patchMeWowDismissed(
  client: ApiClient,
  body: MeWowPatchBody = { dismissed: true },
): Promise<MeWowStateResponse> {
  return client.patchJson<MeWowPatchBody, MeWowStateResponse>("/api/v1/me/wow-state", body);
}
