// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Undelivered messages (LUM-820).
//
// Only PERMANENT bounces reach here — the backend filters 4.x.x, which is a
// retry still in progress. Warning about those is how a warning becomes
// wallpaper and the next real one goes unread.

import type { ApiClient } from "./client";

export interface EmailBounce {
  id: string;
  recipient: string;
  status: string;
  /** Null when the send row has been pruned; the bounce is still worth showing. */
  subject: string | null;
  seen_at: string | null;
  sent_at: string | null;
}

export interface BouncesResponse {
  bounces: EmailBounce[];
}

export async function fetchBounces(api: ApiClient): Promise<EmailBounce[]> {
  const res = await api.getJson<BouncesResponse>("/api/v1/email/bounces");
  return res?.bounces ?? [];
}

export async function acknowledgeBounce(api: ApiClient, id: string): Promise<void> {
  await api.postJson<Record<string, never>, unknown>(
    `/api/v1/email/bounces/${encodeURIComponent(id)}/acknowledge`,
    {},
  );
}
