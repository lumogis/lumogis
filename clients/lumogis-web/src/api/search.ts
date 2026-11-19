// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Hand-written DTOs for memory search + KG endpoints.
// Mirrors orchestrator/models/api_v1.py (MemorySearchHit, EntityCard,
// RelatedEntity, RecentSession) per parent plan §"Phase 1 Pass 1.3".
//
// These are kept hand-written intentionally until full codegen runs in CI
// (Pass 1.1 item 1 generated stubs; these typed wrappers layer on top).

import { ApiError, type ApiClient } from "./client";

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

// LUM-581 — household entity sharing lifecycle. Entity publish is synchronous
// (single Qdrant point, no background job), so — unlike documents — only
// ``personal``/``shared`` occur (no ``sharing``/``unsharing``/``partial``).
export type EntityShareStatus = "personal" | "shared";

export interface EntityCard {
  entity_id: string;
  name: string;
  type: string | null;
  aliases: string[];
  summary: string | null;
  sources: string[];
  scope: MemoryScope;
  owner_user_id: string | null;
  // LUM-581 — additive + defaulted server-side, so older payloads still parse.
  share_status?: EntityShareStatus;
  is_owner?: boolean;
  /** LUM-161 — optimistic-concurrency version; sent back by the edit dialog. */
  version?: number | null;
  /**
   * LUM-966 — the caller hid this (another member's shared) entry from her own
   * view. Only the full card (`getEntity`) is authoritative; search stubs never
   * carry it.
   */
  hidden?: boolean;
}

/** True when the entity is shared with the household (drives badge + filter). */
export function isEntityShared(status: EntityShareStatus | undefined): boolean {
  return status === "shared";
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

// ── LUM-161 — "What Lumogis knows": list, mentions, rename ───────────────

export type EntityKind = "person" | "org" | "project" | "concept" | "other";

export interface EntityListItem {
  entity_id: string;
  name: string;
  type: string | null;
  kind: EntityKind;
  /** First stored aliases (the server returns at most 5). */
  aliases: string[];
  /** Total stored aliases. */
  alias_count: number;
  scope: MemoryScope;
  share_status: EntityShareStatus;
  is_owner: boolean;
  /** Items that mention it and still exist; null unless the caller owns the row. */
  mention_count: number | null;
  /** null unless the caller owns the row. */
  last_mentioned_at: string | null;
}

export interface EntityKindCounts {
  person: number;
  org: number;
  project: number;
  concept: number;
  other: number;
}

export interface EntityListResponse {
  entities: EntityListItem[];
  /** All filters except `kind`. */
  counts: EntityKindCounts;
  /** All filters including `kind` (for paging). */
  total: number;
}

export type EntityListShared = "only" | "exclude";
export type EntityListSort = "mentions" | "recent" | "name";

export interface ListEntitiesParams {
  kind?: EntityKind;
  shared?: EntityListShared;
  sort?: EntityListSort;
  limit?: number;
  offset?: number;
}

// Only the kinds a shipping writer produces; extend when a writer exists.
export type EntityMentionKind = "document" | "chat" | "ai_tool";

export interface EntityMention {
  kind: EntityMentionKind;
  ref_id: string;
  title: string;
  mentioned_at: string;
}

export interface EntityMentionCounts {
  document: number;
  chat: number;
  ai_tool: number;
}

export interface EntityMentionsResponse {
  mentions: EntityMention[];
  counts: EntityMentionCounts;
  total: number;
}

/** The kinds a member can change an entry to (LUM-967); the server accepts them case-insensitively. */
export type EntityTypeValue = "PERSON" | "ORG" | "PROJECT" | "CONCEPT";

/**
 * PATCH body: alias *deltas* applied server-side to the locked row (Decision 8),
 * or `entity_type` alone to change the entry's kind (LUM-967) — one change at a
 * time (`one_change_at_a_time` otherwise).
 */
export interface EntityEditRequest {
  name?: string;
  aliases_add?: string[];
  aliases_remove?: string[];
  entity_type?: EntityTypeValue;
  version: number;
}

export type EntityEditError =
  | { code: "version_conflict"; currentVersion: number | null; which?: EntityWhich }
  | {
      code: "undo_newer_first";
      newerMergeId: string | null;
      newerOtherName: string | null;
    }
  | {
      code: "name_in_use";
      conflictingEntityId: string | null;
      conflictingName: string | null;
      conflictingIsStaged: boolean;
      /** Kind of the clashing entry; set by a kind change, null for a rename (LUM-967). */
      conflictingEntityType: string | null;
      /** The name or other name that clashes (`detail.name`). */
      name: string | null;
    }
  | {
      code:
        | "nothing_to_change"
        | "empty_name"
        | "invalid_characters"
        | "too_many_aliases"
        | "invalid_kind"
        | "one_change_at_a_time"
        | "not_owner"
        | "not_editable"
        | "entity_not_found"
        | "store_unavailable"
        // LUM-965 — merge, undo and move-mentions refusals.
        | "same_entry"
        | "staged_entry"
        | "sharing_differs"
        | "already_undone"
        | "merge_not_found"
        | "loser_id_taken"
        | "relation_not_found"
        // LUM-966 — delete, hide and the won't-learn list.
        | "own_entry"
        | "rule_not_found";
      /** Which entry a merge/move refusal is about (`this` = the entry whose page it is). */
      which?: EntityWhich;
    };

/** Which of the two entries a merge refusal is about. */
export type EntityWhich = "this" | "other";

const _EDIT_ERROR_CODES = new Set([
  "same_entry",
  "staged_entry",
  "sharing_differs",
  "already_undone",
  "merge_not_found",
  "loser_id_taken",
  "relation_not_found",
  "own_entry",
  "rule_not_found",
  "nothing_to_change",
  "empty_name",
  "invalid_characters",
  "too_many_aliases",
  "invalid_kind",
  "one_change_at_a_time",
  "not_owner",
  "not_editable",
  "entity_not_found",
  "store_unavailable",
]);

/**
 * Decode a failed {@link editEntity} call. Reads `ApiError.code` and, for
 * `version_conflict` / `name_in_use`, the extra fields in the body
 * (`ApiError.detail` is `JSON.stringify(body)`, so they sit under `.detail`).
 * Returns null for anything that is not a documented edit error.
 */
export function parseEntityEditError(e: unknown): EntityEditError | null {
  if (!(e instanceof ApiError)) return null;
  const code = e.code;
  if (!code) return null;
  let inner: Record<string, unknown> = {};
  try {
    const parsed = JSON.parse(e.detail) as { detail?: unknown };
    if (parsed && typeof parsed.detail === "object" && parsed.detail !== null) {
      inner = parsed.detail as Record<string, unknown>;
    }
  } catch {
    // detail was not JSON; code alone is still usable
  }
  const which: EntityWhich | undefined =
    inner.which === "this" || inner.which === "other" ? inner.which : undefined;
  if (code === "version_conflict") {
    return {
      code,
      currentVersion: typeof inner.current_version === "number" ? inner.current_version : null,
      which,
    };
  }
  if (code === "undo_newer_first") {
    return {
      code,
      newerMergeId: typeof inner.newer_merge_id === "string" ? inner.newer_merge_id : null,
      newerOtherName:
        typeof inner.newer_other_name === "string" ? inner.newer_other_name : null,
    };
  }
  if (code === "name_in_use") {
    return {
      code,
      conflictingEntityId:
        typeof inner.conflicting_entity_id === "string" ? inner.conflicting_entity_id : null,
      conflictingName:
        typeof inner.conflicting_name === "string" ? inner.conflicting_name : null,
      conflictingIsStaged: inner.conflicting_is_staged === true,
      conflictingEntityType:
        typeof inner.conflicting_entity_type === "string" ? inner.conflicting_entity_type : null,
      name: typeof inner.name === "string" ? inner.name : null,
    };
  }
  if (_EDIT_ERROR_CODES.has(code)) {
    return { code, which } as EntityEditError;
  }
  return null;
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

export async function listEntities(
  client: ApiClient,
  params: ListEntitiesParams = {},
  signal?: AbortSignal,
): Promise<EntityListResponse> {
  const qs = new URLSearchParams();
  if (params.kind) qs.set("kind", params.kind);
  if (params.shared) qs.set("shared", params.shared);
  if (params.sort) qs.set("sort", params.sort);
  if (params.limit !== undefined) qs.set("limit", String(params.limit));
  if (params.offset !== undefined) qs.set("offset", String(params.offset));
  const q = qs.toString();
  return client.getJson<EntityListResponse>(`/api/v1/kg/entities${q ? `?${q}` : ""}`, {
    signal,
  });
}

export async function getEntityMentions(
  client: ApiClient,
  entityId: string,
  page: { limit?: number; offset?: number } = {},
  signal?: AbortSignal,
): Promise<EntityMentionsResponse> {
  const qs = new URLSearchParams();
  if (page.limit !== undefined) qs.set("limit", String(page.limit));
  if (page.offset !== undefined) qs.set("offset", String(page.offset));
  const q = qs.toString();
  return client.getJson<EntityMentionsResponse>(
    `/api/v1/kg/entities/${encodeURIComponent(entityId)}/mentions${q ? `?${q}` : ""}`,
    { signal },
  );
}

/** Rename an entry, edit its other names, or change its kind (owner only) → the updated card. */
export async function editEntity(
  client: ApiClient,
  entityId: string,
  body: EntityEditRequest,
): Promise<EntityCard> {
  return client.patchJson<EntityEditRequest, EntityCard>(
    `/api/v1/kg/entities/${encodeURIComponent(entityId)}`,
    body,
  );
}

// ── Household sharing (LUM-581) ───────────────────────────────────────────
//
// Note the path split: the entity READ surface is `/api/v1/kg/entities/{id}`
// while publish/unpublish target `/api/v1/entities/{id}/publish` (the Postgres
// `entities` table). Both use the same `entities.entity_id` UUID — no id
// translation is needed (confirmed against the shared PK).

/** Share a personal entity with the household (LUM-581) → 200/204 (synchronous). */
export async function publishEntity(
  client: ApiClient,
  entityId: string,
): Promise<void> {
  await client.postJson<{ scope: "shared" }, unknown>(
    `/api/v1/entities/${encodeURIComponent(entityId)}/publish`,
    { scope: "shared" },
  );
}

/** Stop sharing an entity with the household (LUM-581) → 200/204 (synchronous). */
export async function unpublishEntity(
  client: ApiClient,
  entityId: string,
): Promise<void> {
  await client.delete<void>(
    `/api/v1/entities/${encodeURIComponent(entityId)}/publish`,
  );
}


// ── Merge two entries, with undo (LUM-965) ────────────────────────────────

export type MergeOrigin = "member" | "auto_dedup" | "review_queue" | "admin";

export interface EntityMergeRequest {
  other_id: string;
  /** The surviving (this page's) entry's version. */
  version: number;
  other_version: number;
  keep_name: "this" | "other";
}

export interface EntityMergeResponse {
  entry: EntityCard;
  merge_id: string;
}

export interface EntityMergeHistoryItem {
  merge_id: string;
  /** `merged_in`: this entry is the survivor. `merged_out`: it was merged away, then restored. */
  direction: "merged_in" | "merged_out";
  other_entity_id: string;
  other_name: string;
  origin: MergeOrigin;
  merged_at: string;
  undone_at: string | null;
  can_undo: boolean;
  /** Set on an open merge that is not the newest: the merge to undo first. */
  newer_merge_id: string | null;
  /** Undo would put a shared copy of the other entry back. */
  restores_sharing: boolean;
}

export interface EntityHistoryResponse {
  items: EntityMergeHistoryItem[];
}

/** A mention the survivor gained since the merge; `relation_id` is what a move names. */
export interface EntityMovableMention extends EntityMention {
  relation_id: number;
}

export interface EntityUndoMergeResponse {
  restored: EntityCard;
  survivor: EntityCard;
  mentions_since: EntityMovableMention[];
  /** Recorded rows that could not be put back (deleted or moved on since). */
  skipped_rows: number;
}

export interface EntityMoveMentionsRequest {
  to_entity_id: string;
  relation_ids: number[];
  /** The source (this page's) entry's version. */
  version: number;
  to_version: number;
}

export interface EntityMoveMentionsResponse {
  moved: number;
  source: EntityCard;
  target: EntityCard;
}

/** Merge another of the member's own entries into this one (this one survives). */
export async function mergeEntity(
  client: ApiClient,
  entityId: string,
  body: EntityMergeRequest,
): Promise<EntityMergeResponse> {
  return client.postJson<EntityMergeRequest, EntityMergeResponse>(
    `/api/v1/kg/entities/${encodeURIComponent(entityId)}/merge`,
    body,
  );
}

/** Merges into (and, once undone, out of) an entry, newest first (owner only). */
export async function getEntityHistory(
  client: ApiClient,
  entityId: string,
  signal?: AbortSignal,
): Promise<EntityHistoryResponse> {
  return client.getJson<EntityHistoryResponse>(
    `/api/v1/kg/entities/${encodeURIComponent(entityId)}/history`,
    { signal },
  );
}

/** Undo one merge (newest first per entry). */
export async function undoMerge(
  client: ApiClient,
  mergeId: string,
): Promise<EntityUndoMergeResponse> {
  return client.postJson<Record<string, never>, EntityUndoMergeResponse>(
    `/api/v1/kg/merges/${encodeURIComponent(mergeId)}/undo`,
    {},
  );
}

/** Move mentions (by relation id) from this entry to another of the member's own. */
export async function moveMentions(
  client: ApiClient,
  entityId: string,
  body: EntityMoveMentionsRequest,
): Promise<EntityMoveMentionsResponse> {
  return client.postJson<EntityMoveMentionsRequest, EntityMoveMentionsResponse>(
    `/api/v1/kg/entities/${encodeURIComponent(entityId)}/mentions/move`,
    body,
  );
}


// ── Delete, Hide and the names Lumogis won't learn (LUM-966) ───────────────

export interface EntityNotBlocked {
  name: string;
  /** `used_by_other_entry`: another entry of the same kind uses it. `already_corrected`: a kind correction sends it elsewhere. */
  reason: "used_by_other_entry" | "already_corrected";
}

export interface EntityDeleteResponse {
  deleted: boolean;
  entity_id: string;
  /** Display forms, canonical name first. */
  blocked_names: string[];
  not_blocked: EntityNotBlocked[];
  /** A household copy existed and went with the entry. */
  household_copy_removed: boolean;
  merges_removed: number;
}

export interface EntityHideResponse {
  entity_id: string;
  hidden: boolean;
}

export interface NameRuleBlock {
  name_norm: string;
  display_name: string;
  /** The blocked kind, upper case (e.g. `PERSON`). */
  entity_type: string;
  document_count: number;
  created_at: string;
}

export interface NameRuleCorrection {
  name_norm: string;
  display_name: string;
  from_type: string;
  to_type: string;
}

export interface HiddenEntry {
  /** The row the viewer sees (the household copy). */
  entity_id: string;
  name: string;
  type: string | null;
  hidden_at: string;
}

export interface NameRulesResponse {
  blocked: NameRuleBlock[];
  corrections: NameRuleCorrection[];
  hidden: HiddenEntry[];
  /** Uncapped counts: each list is cut at 500, newest first. */
  blocked_total: number;
  corrections_total: number;
  hidden_total: number;
}

export interface RemoveNameRuleRequest {
  kind: "block" | "retype";
  name_norm: string;
  /** block: the blocked kind; retype: the kind the correction starts from. */
  entity_type: string;
}

export interface RereadDocument {
  document_id: number;
  title: string;
}

export interface RemoveNameRuleResponse {
  removed: boolean;
  restored_corrections: NameRuleCorrection[];
  /** The caller's current documents only (at most 50, most recent mention first). */
  reread_documents: RereadDocument[];
  reread_more: number;
}

/** Delete the caller's own entry; its names go on the won't-learn list (owner only). */
export async function deleteEntity(
  client: ApiClient,
  entityId: string,
  version: number,
): Promise<EntityDeleteResponse> {
  return client.delete<EntityDeleteResponse>(
    `/api/v1/kg/entities/${encodeURIComponent(entityId)}?version=${encodeURIComponent(String(version))}`,
  );
}

/** Hide another member's shared entry from the caller's own list, search and chat. */
export async function hideEntity(
  client: ApiClient,
  entityId: string,
): Promise<EntityHideResponse> {
  return client.postJson<Record<string, never>, EntityHideResponse>(
    `/api/v1/kg/entities/${encodeURIComponent(entityId)}/hide`,
    {},
  );
}

/** Show a hidden entry again. */
export async function unhideEntity(
  client: ApiClient,
  entityId: string,
): Promise<EntityHideResponse> {
  return client.delete<EntityHideResponse>(
    `/api/v1/kg/entities/${encodeURIComponent(entityId)}/hide`,
  );
}

/** The caller's names Lumogis won't learn, kind corrections and hidden entries. */
export async function listNameRules(
  client: ApiClient,
  signal?: AbortSignal,
): Promise<NameRulesResponse> {
  return client.getJson<NameRulesResponse>("/api/v1/kg/name-rules", { signal });
}

/** Remove a name from the won't-learn list (the undo of a delete) or a kind correction. */
export async function removeNameRule(
  client: ApiClient,
  body: RemoveNameRuleRequest,
): Promise<RemoveNameRuleResponse> {
  return client.postJson<RemoveNameRuleRequest, RemoveNameRuleResponse>(
    "/api/v1/kg/name-rules/remove",
    body,
  );
}
