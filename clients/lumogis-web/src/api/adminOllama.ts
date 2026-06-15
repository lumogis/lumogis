// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Typed admin Ollama routes: /api/v1/admin/ollama/* (LUM-451).

import type { ApiClient } from "./client";

export interface OllamaLocalModel {
  name: string;
  size?: number;
  display_name?: string;
  modified_at?: string;
  details?: { parameter_size?: string };
}

export interface OllamaCatalogEntry {
  name: string;
  installed: boolean;
  display_name: string;
  description?: string;
  tags?: string[];
}

export interface OllamaDiscoveryResponse {
  local: OllamaLocalModel[];
  catalog: OllamaCatalogEntry[];
  alias_map: Record<string, string>;
  embedding_model: string;
  default_model: string | null;
}

export interface OllamaDeleteResponse {
  status: string;
  name: string;
}

export type OllamaPullJobStatus = "pending" | "running" | "succeeded" | "failed";

export interface OllamaPullJob {
  job_id: string;
  model_name: string;
  status: OllamaPullJobStatus;
  progress_pct: number | null;
  status_message: string | null;
  error_message: string | null;
  qdrant_init_warning: string | null;
  created_at: string | null;
  started_at: string | null;
  finished_at: string | null;
}

export interface OllamaPullStartResponse {
  status: string;
  job_id: string;
}

export interface OllamaPullActiveResponse {
  job: OllamaPullJob | null;
}

export function fetchOllamaDiscovery(client: ApiClient): Promise<OllamaDiscoveryResponse> {
  return client.getJson<OllamaDiscoveryResponse>("/api/v1/admin/ollama/discovery");
}

export function startOllamaPull(client: ApiClient, name: string): Promise<OllamaPullStartResponse> {
  return client.postJson<{ name: string }, OllamaPullStartResponse>(
    "/api/v1/admin/ollama/pull/async",
    { name },
  );
}

export function fetchOllamaPullJob(client: ApiClient, jobId: string): Promise<OllamaPullJob> {
  return client.getJson<OllamaPullJob>(
    `/api/v1/admin/ollama/pull/jobs/${encodeURIComponent(jobId)}`,
  );
}

export function fetchActiveOllamaPullJob(client: ApiClient): Promise<OllamaPullActiveResponse> {
  return client.getJson<OllamaPullActiveResponse>("/api/v1/admin/ollama/pull/jobs/active");
}

export function deleteOllamaModel(client: ApiClient, name: string): Promise<OllamaDeleteResponse> {
  return client.postJson<{ name: string }, OllamaDeleteResponse>(
    "/api/v1/admin/ollama/delete",
    { name },
  );
}
