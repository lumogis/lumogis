// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-512 — service-health polling hook.

import type { ReactNode } from "react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import type { ApiClient } from "../../../src/api/client";
import type { HealthResponse } from "../../../src/api/health";
import { useServiceHealth } from "../../../src/features/_shared/useServiceHealth";

const fetchHealthMock = vi.fn<() => Promise<HealthResponse>>();

vi.mock("../../../src/api/health", () => ({
  fetchHealth: () => fetchHealthMock(),
}));

function wrapper(qc: QueryClient) {
  return function Wrap({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
  };
}

const client = {} as ApiClient;

describe("useServiceHealth (LUM-512)", () => {
  beforeEach(() => {
    fetchHealthMock.mockReset();
  });

  it("derives per-service booleans from the health snapshot", async () => {
    fetchHealthMock.mockResolvedValue({
      overall: "degraded",
      services: { ollama: "healthy", qdrant: "down", graph: "down" },
    });
    const qc = new QueryClient();
    const { result } = renderHook(() => useServiceHealth(client), { wrapper: wrapper(qc) });

    await waitFor(() => expect(result.current.health).toBeDefined());
    expect(result.current.isOllamaDown).toBe(false);
    expect(result.current.isQdrantDown).toBe(true);
    expect(result.current.isGraphDown).toBe(true);
  });

  it("treats unknown/degraded states as not-down (no scary banner)", async () => {
    fetchHealthMock.mockResolvedValue({
      overall: "degraded",
      services: { ollama: "unknown", qdrant: "degraded" },
    });
    const qc = new QueryClient();
    const { result } = renderHook(() => useServiceHealth(client), { wrapper: wrapper(qc) });

    await waitFor(() => expect(result.current.health).toBeDefined());
    expect(result.current.isOllamaDown).toBe(false);
    expect(result.current.isQdrantDown).toBe(false);
  });

  it("refresh() invalidates the health query (source-of-truth reconcile)", async () => {
    fetchHealthMock.mockResolvedValue({ overall: "ok", services: { ollama: "healthy" } });
    const qc = new QueryClient();
    const invalidateSpy = vi.spyOn(qc, "invalidateQueries");
    const { result } = renderHook(() => useServiceHealth(client), { wrapper: wrapper(qc) });

    await waitFor(() => expect(result.current.health).toBeDefined());
    result.current.refresh();
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["service-health"] });
  });
});
