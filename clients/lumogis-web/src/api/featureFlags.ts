// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Read-only client for the feature-flag visibility endpoint (LUM-126, LUM-573).

import type { ApiClient } from "./client";

export interface FeatureFlagState {
  key: string;
  env_var: string;
  description: string;
  default: boolean;
  enabled: boolean;
}

export interface FeatureFlagsResponse {
  total: number;
  enabled: number;
  flags: FeatureFlagState[];
}

export async function fetchFeatureFlags(client: ApiClient): Promise<FeatureFlagsResponse> {
  return client.getJson<FeatureFlagsResponse>("/api/v1/admin/feature-flags");
}
