// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Search & retrieval admin settings (LUM-159). Reads/writes the reranker
// toggle via the shared legacy `/settings` endpoint.

import type { ApiClient } from "./client";

export interface AdminSearchSettings {
  reranker_enabled: boolean;
  reranker_backend_live: string;
  reranker_pending_restart: boolean;
}

// Estimated additional resident memory for the default BGE reranker model.
export const BGE_RERANKER_RAM_ESTIMATE = "~1.36 GB peak RAM";
export const BGE_RERANKER_DOWNLOAD_ESTIMATE = "~400 MB";
export const BGE_RERANKER_MODEL = "BAAI/bge-reranker-base";

export function isRerankerBackendActive(backend: string): boolean {
  const normalized = backend.trim().toLowerCase();
  return normalized !== "" && !["none", "off", "false", "0"].includes(normalized);
}

function parseAdminSearchSettings(data: Record<string, unknown>): AdminSearchSettings {
  const reranker_backend_live =
    typeof data.reranker_backend_live === "string"
      ? data.reranker_backend_live.trim().toLowerCase()
      : "none";
  return {
    reranker_enabled: Boolean(data.reranker_enabled),
    reranker_backend_live,
    reranker_pending_restart: Boolean(data.reranker_pending_restart),
  };
}

export async function fetchAdminSearchSettings(client: ApiClient): Promise<AdminSearchSettings> {
  const data = await client.getJson<Record<string, unknown>>("/settings");
  return parseAdminSearchSettings(data);
}

export async function putAdminRerankerEnabled(
  client: ApiClient,
  rerankerEnabled: boolean,
): Promise<AdminSearchSettings> {
  const data = await client.putJson<{ reranker_enabled: boolean }, Record<string, unknown>>(
    "/settings",
    { reranker_enabled: rerankerEnabled },
  );
  return parseAdminSearchSettings(data);
}

/** Trigger stack recreate via stack-control (Compose path). Network drop is expected. */
export async function restartStack(client: ApiClient): Promise<void> {
  try {
    await client.fetch("/settings/restart", { method: "POST" });
  } catch {
    // Orchestrator may die mid-request during container recreate.
  }
}
