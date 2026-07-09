// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// The member's own household shared items (LUM-583):
//   GET    /api/v1/me/shared-items                       — list what I've shared
//   DELETE /api/v1/{resource_type}/{resource_id}/publish — unshare one of mine
//
// Unshare reuses the existing owner-only per-type unpublish route (the same
// `scope.py` route each share toggle uses); `resource_type` is the route
// segment and `resource_id` is the source publish pk (`published_from`).

import type { ApiClient } from "./client";

export type ShareResourceType =
  | "notes"
  | "audio_memos"
  | "sessions"
  | "files"
  | "entities"
  | "signals";

export interface SharedItem {
  resource_type: ShareResourceType;
  resource_id: string;
  label: string | null;
  shared_at: string | null;
}

export interface SharedItemsResponse {
  items: SharedItem[];
}

export function listMySharedItems(client: ApiClient): Promise<SharedItemsResponse> {
  return client.getJson<SharedItemsResponse>("/api/v1/me/shared-items");
}

export function unshareMyItem(
  client: ApiClient,
  resourceType: ShareResourceType,
  resourceId: string,
): Promise<void> {
  return client.delete<void>(
    `/api/v1/${resourceType}/${encodeURIComponent(resourceId)}/publish`,
  );
}
