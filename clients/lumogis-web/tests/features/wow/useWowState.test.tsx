// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis

import { describe, expect, it } from "vitest";

/**
 * Mirrors TanStack Query v5 refetchInterval callback in useWowState.ts.
 */
function wowRefetchInterval(query: { state: { data?: { entities_ready?: boolean } } }): false | number {
  return query.state.data?.entities_ready ? false : 4000;
}

describe("useWowState refetchInterval", () => {
  it("polls every 4s until entities_ready", () => {
    expect(wowRefetchInterval({ state: { data: { entities_ready: false } } })).toBe(4000);
    expect(wowRefetchInterval({ state: { data: undefined } })).toBe(4000);
    expect(wowRefetchInterval({ state: { data: { entities_ready: true } } })).toBe(false);
  });
});
