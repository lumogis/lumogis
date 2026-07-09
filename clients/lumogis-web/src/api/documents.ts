// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Document library API client (LUM-160).

import type { ApiClient } from "./client";

export type DocumentStatus = "indexing" | "indexed" | "failed";

// LUM-157 — household document sharing lifecycle. ``partial`` is a
// terminal-but-incomplete share (some sections still indexing).
export type ShareStatus =
  | "personal"
  | "sharing"
  | "shared"
  | "unsharing"
  | "partial";

export interface DocumentSummary {
  document_id: number | null;
  in_flight_job_id?: number | null;
  display_name: string;
  file_path: string;
  file_type: string;
  chunk_count: number;
  entity_count: number;
  scope: string;
  status: DocumentStatus;
  indexed_at: string | null;
  error_message: string | null;
  // LUM-157 — additive + defaulted server-side, so older payloads still parse.
  share_status?: ShareStatus;
  in_flight_share_job_id?: number | null;
  is_owner?: boolean;
  // LUM-585 — "Shared by {member}" attribution (non-owner detail view only).
  shared_by?: string | null;
}

export interface DocumentEntityLink {
  entity_id: string;
  name: string;
  entity_type: string;
}

export interface DocumentDetail extends DocumentSummary {
  file_hash: string | null;
  entities: DocumentEntityLink[];
  source_available: boolean;
}

export interface DocumentListResponse {
  documents: DocumentSummary[];
}

export interface DocumentDeleteResponse {
  document_id: number;
  deleted: boolean;
  partial: boolean;
  errors: string[];
}

export interface ReingestQueuedResponse {
  document_id: number;
  job_id: number;
  queued: boolean;
}

export interface ShareQueuedResponse {
  document_id: number;
  job_id: number;
  share_status: "sharing" | "unsharing";
}

/** True when the share lifecycle is in a state the badge should treat as shared. */
export function isShared(status: ShareStatus | undefined): boolean {
  return status === "shared" || status === "partial";
}

export async function listDocuments(
  client: ApiClient,
  signal?: AbortSignal,
): Promise<DocumentListResponse> {
  return client.getJson<DocumentListResponse>("/api/v1/documents", { signal });
}

export async function getDocument(
  client: ApiClient,
  documentId: number,
  signal?: AbortSignal,
): Promise<DocumentDetail> {
  return client.getJson<DocumentDetail>(
    `/api/v1/documents/${encodeURIComponent(String(documentId))}`,
    { signal },
  );
}

export async function deleteDocument(
  client: ApiClient,
  documentId: number,
): Promise<DocumentDeleteResponse> {
  return client.delete<DocumentDeleteResponse>(
    `/api/v1/documents/${encodeURIComponent(String(documentId))}`,
  );
}

export async function reingestDocument(
  client: ApiClient,
  documentId: number,
  body?: { force?: boolean },
): Promise<ReingestQueuedResponse> {
  return client.postJson<{ force?: boolean }, ReingestQueuedResponse>(
    `/api/v1/documents/${encodeURIComponent(String(documentId))}/reingest`,
    body ?? {},
  );
}

/** Share a personal document with the household (LUM-157) → 202 + job_id. */
export async function publishDocument(
  client: ApiClient,
  documentId: number,
): Promise<ShareQueuedResponse> {
  return client.postJson<Record<string, never>, ShareQueuedResponse>(
    `/api/v1/documents/${encodeURIComponent(String(documentId))}/publish`,
    {},
  );
}

/** Stop sharing a document with the household (LUM-157) → 202 + job_id. */
export async function unpublishDocument(
  client: ApiClient,
  documentId: number,
): Promise<ShareQueuedResponse> {
  return client.delete<ShareQueuedResponse>(
    `/api/v1/documents/${encodeURIComponent(String(documentId))}/publish`,
  );
}
