// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-853 — `pollWhileBusy`: chat only needs "is there any row", so it must not poll while an
// upload is indexing; the /documents page keeps its own 2 s interval (default).

import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, renderHook } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { ApiClient } from "../../../src/api/client";
import { AccessTokenStore } from "../../../src/api/tokens";
import { useDocuments } from "../../../src/features/documents/useDocuments";

const indexingRow = {
  document_id: 1,
  in_flight_job_id: 7,
  display_name: "notes.md",
  file_path: "/library/notes.md",
  file_type: ".md",
  chunk_count: 0,
  entity_count: 0,
  scope: "personal",
  status: "indexing",
  indexed_at: null,
  error_message: null,
};

function setup() {
  const fetchImpl = vi.fn(
    async () =>
      new Response(JSON.stringify({ documents: [indexingRow] }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
  );
  const client = new ApiClient({
    tokens: new AccessTokenStore(),
    fetchImpl: fetchImpl as unknown as typeof fetch,
  });
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  );
  return { fetchImpl, client, wrapper };
}

describe("useDocuments pollWhileBusy (LUM-853)", () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: false });
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("12 does not poll while busy when pollWhileBusy is false", async () => {
    const { fetchImpl, client, wrapper } = setup();
    renderHook(() => useDocuments(client, { pollWhileBusy: false }), { wrapper });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    expect(fetchImpl).toHaveBeenCalledTimes(1);
  });

  it("12b polls while busy by default", async () => {
    const { fetchImpl, client, wrapper } = setup();
    renderHook(() => useDocuments(client), { wrapper });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    expect(fetchImpl.mock.calls.length).toBeGreaterThan(1);
  });
});
