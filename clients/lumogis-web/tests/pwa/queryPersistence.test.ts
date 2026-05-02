// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis

import { describe, expect, it } from "vitest";
import { dehydrate, QueryClient } from "@tanstack/react-query";

import packageJson from "../../package.json";
import {
  PERSISTED_QUERY_MAX_AGE_MS,
  QUERY_PERSIST_LOCALSTORAGE_KEY,
  getQueryPersistenceBuster,
  isPersistableQueryKey,
  isQueryPersistenceRuntimeEnabled,
  queryKeyMatchesAllowlist,
  shouldDehydrateQueryForPersist,
} from "../../src/pwa/queryPersistence";

describe("query persistence (Phase 3D)", () => {
  it("buster matches package.json version", () => {
    expect(getQueryPersistenceBuster()).toBe(packageJson.version);
  });

  it("maxAge is a finite conservative window", () => {
    expect(Number.isFinite(PERSISTED_QUERY_MAX_AGE_MS)).toBe(true);
    expect(PERSISTED_QUERY_MAX_AGE_MS).toBeGreaterThan(0);
    expect(PERSISTED_QUERY_MAX_AGE_MS).toBeLessThanOrEqual(7 * 24 * 60 * 60 * 1000);
  });

  it("storage key uses lumogis namespace", () => {
    expect(QUERY_PERSIST_LOCALSTORAGE_KEY.startsWith("lumogis:")).toBe(true);
  });

  it("allowlist matcher returns true for an exact tuple prefix match", () => {
    expect(
      queryKeyMatchesAllowlist(
        ["memory", "recent-query-text", "extra"],
        [["memory", "recent-query-text"]],
      ),
    ).toBe(true);
  });

  it("unknown keys do not match an empty production allowlist", () => {
    expect(isPersistableQueryKey(["anything", "else"])).toBe(false);
  });

  it("explicitly unsafe domains are not persisted (empty allowlist → false)", () => {
    const unsafe: Parameters<typeof isPersistableQueryKey>[0][] = [
      ["auth", "me"],
      ["chat", "completions"],
      ["chat", "threads"],
      ["approvals", "pending"],
      ["audit", "list"],
      ["admin", "users"],
      ["admin", "audit"],
      ["admin", "diagnostics", "summary"],
      ["credentials", "x"],
      ["cc", "list", "/api/foo"],
      ["cc", "registry"],
      ["mcp", "me"],
      ["mcp", "admin", "user-id"],
      ["me", "permissions"],
      ["me", "llm-providers"],
    ];
    unsafe.forEach((k) => expect(isPersistableQueryKey(k)).toBe(false));
  });

  it("shouldDehydrateQuery rejects non-success queries", () => {
    expect(
      shouldDehydrateQueryForPersist({
        queryKey: ["meta", "ok"],
        state: { status: "pending" } as never,
      }),
    ).toBe(false);
  });

  it("dehydrated state excludes prefetch when allowlist is empty", async () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    await qc.prefetchQuery({
      queryKey: ["auth", "me"],
      queryFn: async () => ({ email: "a@example.com", id: "1", role: "user" as const }),
    });
    const state = dehydrate(qc, {
      shouldDehydrateQuery: shouldDehydrateQueryForPersist,
      shouldDehydrateMutation: () => false,
    });
    expect(state.queries).toHaveLength(0);
    expect(state.mutations).toHaveLength(0);
  });

  it("Vitest disables runtime persistence hooks", () => {
    expect(isQueryPersistenceRuntimeEnabled()).toBe(false);
  });
});
