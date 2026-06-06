// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Thomas Kohlborn, trading as Lumogis

import { invoke } from "@tauri-apps/api/core";

export type MemoryScope = "personal" | "shared" | "system";

export interface MemorySearchHit {
  id: string;
  score: number;
  title?: string | null;
  snippet: string;
  source?: string | null;
  created_at?: string | null;
  scope: MemoryScope;
  owner_user_id?: string | null;
}

export interface MemorySearchResponse {
  hits: MemorySearchHit[];
  degraded: boolean;
  reason?: string | null;
}

export function normaliseOrchestratorBaseUrl(raw: string): string {
  let s = raw.trim();
  while (s.endsWith("/")) {
    s = s.slice(0, -1);
  }
  return s;
}

export function buildMemorySearchUrl(baseUrl: string, q: string): string {
  const base = normaliseOrchestratorBaseUrl(baseUrl);
  const u = new URL("/api/v1/memory/search", base);
  u.searchParams.set("q", q);
  u.searchParams.set("limit", "5");
  return u.toString();
}

function mapHttpStatusMessage(status: number): string {
  if (status === 401) return "missing_or_invalid_session_token";
  if (status === 403) return "forbidden";
  if (status === 429) return "rate_limited";
  if (status >= 500) return "server_error";
  return `http_${status}`;
}

export async function parseMemorySearchResponse(res: Response): Promise<MemorySearchResponse> {
  const status = res.status;
  const ct = res.headers.get("content-type") ?? "";
  const text = await res.text();
  if (status === 401) throw new Error(mapHttpStatusMessage(401));
  if (status === 403) throw new Error(mapHttpStatusMessage(403));
  if (status === 429) throw new Error(mapHttpStatusMessage(429));
  if (status >= 500) throw new Error(mapHttpStatusMessage(status));
  if (!res.ok) throw new Error(mapHttpStatusMessage(status));
  if (!ct.includes("json") && text.trimStart().startsWith("<")) {
    throw new Error("non_json_response");
  }
  let body: unknown;
  try {
    body = JSON.parse(text);
  } catch {
    throw new Error("invalid_json");
  }
  return body as MemorySearchResponse;
}

export async function fetchMemorySearchWithFetch(
  baseUrl: string,
  token: string | null,
  q: string,
  signal: AbortSignal,
  fetchImpl: typeof fetch = globalThis.fetch.bind(globalThis),
): Promise<MemorySearchResponse> {
  const url = buildMemorySearchUrl(baseUrl, q);
  const headers: Record<string, string> = {};
  if (token && token.length > 0) {
    headers.Authorization = `Bearer ${token}`;
  }
  const res = await fetchImpl(url, { signal, headers });
  return parseMemorySearchResponse(res);
}

export function runningInTauri(): boolean {
  if (import.meta.env.MODE === "test") {
    return false;
  }
  return typeof globalThis !== "undefined" && "__TAURI_INTERNALS__" in (globalThis as object);
}

export async function fetchMemorySearch(
  baseUrl: string,
  token: string | null,
  q: string,
  signal: AbortSignal,
): Promise<MemorySearchResponse> {
  if (runningInTauri()) {
    void baseUrl;
    void token;
    void signal;
    return invoke<MemorySearchResponse>("search_memory", { q });
  }
  return fetchMemorySearchWithFetch(baseUrl, token, q, signal);
}

export function clampSnippet(s: string, max = 160): string {
  if (s.length <= max) return s;
  return `${s.slice(0, max - 1)}…`;
}
