// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Read-only GET /api/v1/admin/diagnostics/update-status.

import type { ApiClient } from "./client";

export interface UpdateStatusResponse {
  current_version: string;
  latest_version: string | null;
  update_available: boolean;
  checked: boolean;
  checked_at: string | null;
  release_url: string | null;
  error: string | null;
}

export function fetchAdminUpdateStatus(client: ApiClient): Promise<UpdateStatusResponse> {
  return client.getJson<UpdateStatusResponse>("/api/v1/admin/diagnostics/update-status");
}
