// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Typed façade for GET/PATCH /api/v1/me/onboarding (LUM-165).

import type { ApiClient } from "./client";

export interface MeOnboardingResponse {
  completed_at: string | null;
}

export interface MeOnboardingPatchBody {
  completed: true;
}

export function getMeOnboarding(client: ApiClient): Promise<MeOnboardingResponse> {
  return client.getJson<MeOnboardingResponse>("/api/v1/me/onboarding");
}

export function patchMeOnboardingComplete(
  client: ApiClient,
  body: MeOnboardingPatchBody = { completed: true },
): Promise<MeOnboardingResponse> {
  return client.patchJson<MeOnboardingPatchBody, MeOnboardingResponse>("/api/v1/me/onboarding", body);
}
