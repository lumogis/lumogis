// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Phase 3D — bounded TanStack Query persistence (Pass 3.4).
// Persist only explicitly allowlisted non-sensitive **read** query keys.
// Auth, chat, admin, credentials, MCP tokens, approvals, audit, etc. must never appear here.

import { createSyncStoragePersister } from "@tanstack/query-sync-storage-persister";
import type { PersistQueryClientOptions } from "@tanstack/query-persist-client-core";
import type { Query } from "@tanstack/react-query";
import type { QueryKey } from "@tanstack/react-query";

import packageJson from "../../package.json";

/** One day — drop persisted cache if older than this (restore + save paths). */
export const PERSISTED_QUERY_MAX_AGE_MS = 24 * 60 * 60 * 1000;

/** Single localStorage slot for the dehydrated blob (filtered by allowlist). */
export const QUERY_PERSIST_LOCALSTORAGE_KEY = "lumogis:query-cache";

/**
 * Buster: must change when deploy changes query shapes or persistence policy.
 * Tied to `package.json` `version` so each release clears stored query state.
 */
export function getQueryPersistenceBuster(): string {
  return packageJson.version;
}

/**
 * Explicit allowlist as **prefix tuples** (every segment must match in order).
 * **Currently empty** — no feature query payloads are vetted for disk yet; all
 * queries stay in memory only until a safe read-cache is added with a stable key.
 */
export const PERSISTABLE_QUERY_KEY_PREFIXES: readonly (readonly unknown[])[] = [];

/**
 * Predicate used by PersistQueryClient `dehydrateOptions.shouldDehydrateQuery`.
 * Unknown keys → false; mutations handled via `shouldDehydrateMutation: () => false`.
 */
export function shouldDehydrateQueryForPersist(query: Pick<Query, "queryKey" | "state">): boolean {
  if (query.state.status !== "success") return false;
  return isPersistableQueryKey(query.queryKey);
}

/** Match `queryKey` against an allowlist of prefix tuples (for tests / future tuning). */
export function queryKeyMatchesAllowlist(
  queryKey: QueryKey,
  allowedPrefixes: readonly (readonly unknown[])[],
): boolean {
  const key = Array.isArray(queryKey) ? queryKey : [queryKey];
  return allowedPrefixes.some((prefix) => {
    if (prefix.length > key.length) return false;
    return prefix.every((segment, i) => Object.is(key[i], segment));
  });
}

export function isPersistableQueryKey(queryKey: QueryKey): boolean {
  return queryKeyMatchesAllowlist(queryKey, PERSISTABLE_QUERY_KEY_PREFIXES);
}

/**
 * Browser + prod/dev only — skipped under Vitest (`import.meta.env.MODE === 'test'`)
 * and SSR (`window` absent) so tests stay deterministic and avoid touching storage.
 */
export function isQueryPersistenceRuntimeEnabled(): boolean {
  if (typeof window === "undefined") return false;
  if (import.meta.env.MODE === "test") return false;
  try {
    return typeof window.localStorage !== "undefined";
  } catch {
    return false;
  }
}

/** Full persist options except `queryClient` (injected by `PersistQueryClientProvider`). */
export function createQueryPersistenceOptions(): Omit<PersistQueryClientOptions, "queryClient"> {
  const persister = createSyncStoragePersister({
    storage: window.localStorage,
    key: QUERY_PERSIST_LOCALSTORAGE_KEY,
  });
  return {
    persister,
    maxAge: PERSISTED_QUERY_MAX_AGE_MS,
    buster: getQueryPersistenceBuster(),
    dehydrateOptions: {
      shouldDehydrateQuery: shouldDehydrateQueryForPersist,
      shouldDehydrateMutation: () => false,
    },
  };
}
