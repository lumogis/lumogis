// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Per-mailbox outgoing signature (LUM-817).
//
// Read-only from the composer's point of view: the settings surface writes it,
// the composer seeds it into the visible body so the user can edit or delete it
// for a single message.

import type { ApiClient } from "./client";

export interface SignatureResponse {
  signature: string;
}

export async function fetchSignature(api: ApiClient, sourceId: string): Promise<string> {
  const res = await api.getJson<SignatureResponse>(
    `/api/v1/email/sources/${encodeURIComponent(sourceId)}/signature`,
  );
  return res?.signature ?? "";
}

export async function saveSignature(
  api: ApiClient,
  sourceId: string,
  signature: string,
): Promise<string> {
  const res = await api.putJson<{ signature: string }, SignatureResponse>(
    `/api/v1/email/sources/${encodeURIComponent(sourceId)}/signature`,
    { signature },
  );
  return res?.signature ?? "";
}
