// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiClient } from "../../../src/api/client";
import { AccessTokenStore } from "../../../src/api/tokens";
import { pollOrchestratorHealth } from "../../../src/features/admin/pollOrchestratorHealth";

describe("pollOrchestratorHealth", () => {
  let originalFetch: typeof fetch;

  beforeEach(() => {
    originalFetch = globalThis.fetch;
    vi.useFakeTimers();
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("returns ok on the third poll attempt", async () => {
    let calls = 0;
    const fetchImpl = vi.fn(async () => {
      calls += 1;
      return { ok: calls >= 3 } as Response;
    }) as unknown as typeof fetch;
    globalThis.fetch = fetchImpl;

    const store = new AccessTokenStore();
    const client = new ApiClient({ tokens: store, fetchImpl });

    const promise = pollOrchestratorHealth(client, 10_000, 100);
    await vi.advanceTimersByTimeAsync(300);
    const result = await promise;

    expect(result.ok).toBe(true);
    expect(calls).toBe(3);
  });
});
