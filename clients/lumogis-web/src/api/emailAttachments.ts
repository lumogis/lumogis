// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Client for the LUM-695 attachment surface.
// Mirrors orchestrator/routes/api_v1/email_attachments.py.
//
// Two contracts worth carrying into the UI rather than flattening:
//
//   * `available: false` is NOT "no attachments". A metadata-mode mailbox never
//     fetched bodies, so it cannot know — and rendering an empty list would be
//     an outright lie about a message that has three. Callers must branch.
//   * 503 and 410 are different. The mailbox being unreachable is retryable;
//     the message being deleted upstream never will be. That distinction is the
//     honest cost of fetch-on-demand, and collapsing it into one error message
//     makes a deliberate trade look like a bug.

import { ApiError, type ApiClient } from "./client";

// A module-local copy, matching the convention in captures.ts / ingest.ts /
// voice.ts — each multipart client owns one rather than sharing an export.
async function readErrorPayload(res: Response): Promise<string> {
  try {
    const body = (await res.clone().json()) as { detail?: unknown };
    if (typeof body?.detail === "string") return body.detail;
    return JSON.stringify(body?.detail ?? body);
  } catch {
    try {
      return await res.text();
    } catch {
      return res.statusText || "request failed";
    }
  }
}

export interface AttachmentRow {
  part_index: number;
  filename: string;
  mime_type: string;
  size_bytes: number;
}

export interface AttachmentListResponse {
  rows: AttachmentRow[];
  /** false for a metadata-mode mailbox — "cannot know", not "none". */
  available: boolean;
}

export interface StagedAttachment {
  id: string;
  filename: string;
  mime_type: string;
  size_bytes: number;
}

/** Attachment metadata for one of the caller's own signals. */
export async function listAttachments(
  client: ApiClient,
  signalId: string,
  signal?: AbortSignal,
): Promise<AttachmentListResponse> {
  return client.getJson<AttachmentListResponse>(
    `/api/v1/email/signals/${encodeURIComponent(signalId)}/attachments`,
    { signal },
  );
}

/** The download PATH for one attachment (relative — `ApiClient` prefixes baseUrl). */
export function attachmentDownloadPath(signalId: string, partIndex: number): string {
  return `/api/v1/email/signals/${encodeURIComponent(signalId)}/attachments/${partIndex}`;
}

/**
 * Download one attachment and hand the browser a blob.
 *
 * This deliberately does NOT render as a plain `<a href>`: auth here is an
 * in-memory `Authorization: Bearer` token, not a cookie, so a link navigation
 * carries no credentials and would 401 for every user under `AUTH_ENABLED=true`.
 * Fetch-then-`createObjectURL` is the pattern the shipped authenticated
 * downloads already use (`MeExportView`, `AdminUsersView`), and it also lets us
 * honour `ApiClient`'s `baseUrl` and read the status codes the route is careful
 * to distinguish.
 *
 * Uses `fetchOnce` (no 401 refresh-retry) because retrying a partial transfer
 * would hide the boundary from the caller — the same reason the other download
 * paths use it.
 *
 * Rejects with `ApiError` carrying the route's status: 503 (mailbox
 * unreachable — retryable), 410 (message gone — never), 413, 415, 429, 404.
 */
export async function downloadAttachment(
  client: ApiClient,
  signalId: string,
  partIndex: number,
  filename: string,
): Promise<void> {
  const res = await client.fetchOnce(attachmentDownloadPath(signalId, partIndex));
  if (!res.ok) {
    throw new ApiError(res.status, await readErrorPayload(res));
  }
  const objectUrl = URL.createObjectURL(await res.blob());
  try {
    const a = document.createElement("a");
    a.href = objectUrl;
    // The server's own Content-Disposition is authoritative for the name; this
    // is the client-side hint for the same (already sanitised) value.
    a.download = filename;
    a.click();
  } finally {
    URL.revokeObjectURL(objectUrl);
  }
}

/**
 * Stage one file for a later send. Resolves with a REFERENCE — the composer
 * puts `id` in the action payload, never the bytes.
 *
 * Rejects with `ApiError` on 413 (too large, or the per-user staging quota),
 * 415, 429 (upload rate) or 507 (could not write).
 */
export async function stageAttachment(
  client: ApiClient,
  file: File,
  signal?: AbortSignal,
): Promise<StagedAttachment> {
  const fd = new FormData();
  fd.append("file", file);
  const res = await client.fetch("/api/v1/email/attachments/stage", {
    method: "POST",
    body: fd,
    signal,
  });
  if (!res.ok) {
    throw new ApiError(res.status, await readErrorPayload(res));
  }
  return (await res.json()) as StagedAttachment;
}

/** Drop a staged file the user removed from the composer before sending. */
export async function discardStagedAttachment(
  client: ApiClient,
  stagedId: string,
): Promise<void> {
  await client.delete(`/api/v1/email/attachments/stage/${encodeURIComponent(stagedId)}`);
}

/** Human-readable byte size for the picker and the approval card. */
export function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) return "";
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB"];
  let value = bytes / 1024;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value < 10 ? value.toFixed(1) : Math.round(value)} ${units[unit]}`;
}
