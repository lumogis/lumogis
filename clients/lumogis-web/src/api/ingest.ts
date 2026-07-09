// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Ingest upload + progress poll API (LUM-511).

import { ApiError, type ApiClient } from "./client";

export type IngestProgressStage =
  | "queued"
  | "extracting"
  | "chunking"
  | "embedding"
  | "graph"
  // LUM-157 — share_document job stages.
  | "projecting"
  | "partial"
  | "done"
  | "failed";

export interface IngestUploadQueuedResponse {
  status: "queued";
  file_id: string;
  job_id: number;
}

export interface IngestJobProgress {
  job_id: number;
  file_id: string | null;
  batch_id: string | null;
  status: string;
  stage: IngestProgressStage;
  progress_pct: number | null;
  status_message: string | null;
  error: string | null;
  enqueued_at: string | null;
  started_at: string | null;
  finished_at: string | null;
}

export interface IngestBatchSummary {
  batch_id: string;
  completed: number;
  failed: number;
  in_progress: number;
}

export async function uploadIngestFile(
  client: ApiClient,
  file: File,
  opts?: { batchId?: string; signal?: AbortSignal },
): Promise<IngestUploadQueuedResponse> {
  const fd = new FormData();
  fd.append("file", file);
  const headers = new Headers();
  if (opts?.batchId) {
    headers.set("X-Lumogis-Batch-Id", opts.batchId);
  }
  const res = await client.fetch("/api/v1/ingest/upload", {
    method: "POST",
    body: fd,
    headers,
    signal: opts?.signal,
  });
  if (!res.ok) {
    throw new ApiError(res.status, await readErrorPayload(res));
  }
  return (await res.json()) as IngestUploadQueuedResponse;
}

export async function getIngestJob(
  client: ApiClient,
  jobId: number,
  signal?: AbortSignal,
): Promise<IngestJobProgress> {
  return client.getJson<IngestJobProgress>(
    `/api/v1/ingest/jobs/${encodeURIComponent(String(jobId))}`,
    { signal },
  );
}

export async function getIngestBatch(
  client: ApiClient,
  batchId: string,
  signal?: AbortSignal,
): Promise<IngestBatchSummary> {
  return client.getJson<IngestBatchSummary>(
    `/api/v1/ingest/batches/${encodeURIComponent(batchId)}`,
    { signal },
  );
}

async function readErrorPayload(res: Response): Promise<string> {
  try {
    const body = (await res.clone().json()) as { detail?: unknown };
    if (typeof body?.detail === "string") return body.detail;
    return JSON.stringify(body);
  } catch {
    try {
      return await res.text();
    } catch {
      return res.statusText || "request failed";
    }
  }
}
