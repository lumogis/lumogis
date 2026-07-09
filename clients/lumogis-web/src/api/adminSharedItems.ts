// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Admin household-sharing governance (LUM-584):
//   GET    /api/v1/admin/shared-items                          — list household shares
//   DELETE /api/v1/admin/shared-items/{resource_type}/{resource_id} — retract a member's share
//
// Admin-only (server-gated by require_admin). `resource_id` is the source
// publish pk (`published_from`) surfaced by the list endpoint — no publish
// response exposes it, so the list is how the admin UI obtains the id.

import type { ApiClient } from "./client";

export type AdminShareResourceType =
  | "notes"
  | "audio_memos"
  | "sessions"
  | "files"
  | "entities"
  | "signals";

export interface AdminSharedItem {
  resource_type: AdminShareResourceType;
  resource_id: string;
  source_owner_id: string | null;
  label: string | null;
}

export interface AdminSharedItemsResponse {
  items: AdminSharedItem[];
}

export interface AdminUnshareResult {
  resource_type: string;
  resource_id: string;
  source_owner_id: string | null;
  unshared: boolean;
}

export function fetchAdminSharedItems(client: ApiClient): Promise<AdminSharedItemsResponse> {
  return client.getJson<AdminSharedItemsResponse>("/api/v1/admin/shared-items");
}

export function adminUnshare(
  client: ApiClient,
  resourceType: AdminShareResourceType | string,
  resourceId: string,
): Promise<AdminUnshareResult> {
  return client.delete<AdminUnshareResult>(
    `/api/v1/admin/shared-items/${resourceType}/${encodeURIComponent(resourceId)}`,
  );
}
