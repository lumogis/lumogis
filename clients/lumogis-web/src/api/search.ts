// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Hand-written DTOs for memory search + KG endpoints.
// Mirrors orchestrator/models/api_v1.py (MemorySearchHit, EntityCard,
// RelatedEntity, RecentSession) per parent plan §"Phase 1 Pass 1.3".
//
// These are kept hand-written intentionally until full codegen runs in CI
// (Pass 1.1 item 1 generated stubs; these typed wrappers layer on top).

import type { ApiClient } from "./client";

// ── Memory scope ─────────────────────────────────────────────────────────

export type MemoryScope = "personal" | "shared" | "system";

// ── Memory / search DTOs ─────────────────────────────────────────────────

export interface MemorySearchHit {
  id: string;
  score: number;
  title: string | null;
  snippet: string;
  source: string | null;
  created_at: string | null; // ISO-8601
  scope: MemoryScope;
  owner_user_id: string | null;
}

export interface MemorySearchResponse {
  hits: MemorySearchHit[];
  degraded: boolean;
  reason: string | null;
}

export interface RecentSession {
  session_id: string;
  summary: string;
  ended_at: string; // ISO-8601
}

export interface RecentSessionsResponse {
  sessions: RecentSession[];
}

// ── KG / entity DTOs ─────────────────────────────────────────────────────

export interface EntityCard {
  entity_id: string;
  name: string;
  type: string | null;
  aliases: string[];
  summary: string | null;
  sources: string[];
  scope: MemoryScope;
  owner_user_id: string | null;
}

export interface RelatedEntity {
  entity_id: string;
  name: string;
  relation: string;
  weight: number | null;
}

export interface RelatedEntitiesResponse {
  related: RelatedEntity[];
}

export interface EntitySearchResponse {
  entities: EntityCard[];
}

// ── Fetch helpers ─────────────────────────────────────────────────────────

export async function memorySearch(
  client: ApiClient,
  q: string,
  limit = 10,
  signal?: AbortSignal,
): Promise<MemorySearchResponse> {
  const params = new URLSearchParams({ q, limit: String(limit) });
  return client.getJson<MemorySearchResponse>(
    `/api/v1/memory/search?${params.toString()}`,
    { signal },
  );
}

export async function kgSearch(
  client: ApiClient,
  q: string,
  limit = 10,
  signal?: AbortSignal,
): Promise<EntitySearchResponse> {
  const params = new URLSearchParams({ q, limit: String(limit) });
  return client.getJson<EntitySearchResponse>(
    `/api/v1/kg/search?${params.toString()}`,
    { signal },
  );
}

export async function getEntity(
  client: ApiClient,
  entityId: string,
  signal?: AbortSignal,
): Promise<EntityCard> {
  return client.getJson<EntityCard>(`/api/v1/kg/entities/${encodeURIComponent(entityId)}`, {
    signal,
  });
}

export async function getRelatedEntities(
  client: ApiClient,
  entityId: string,
  limit = 20,
  signal?: AbortSignal,
): Promise<RelatedEntitiesResponse> {
  const params = new URLSearchParams({ limit: String(limit) });
  return client.getJson<RelatedEntitiesResponse>(
    `/api/v1/kg/entities/${encodeURIComponent(entityId)}/related?${params.toString()}`,
    { signal },
  );
}
