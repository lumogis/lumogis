// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis

import type { ReactNode } from "react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { renderHook } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import type { AccessTokenStore } from "../../../src/api/tokens";
import { useWowReadinessSseInvalidation } from "../../../src/features/wow/useWowReadinessSse";

const openReconnectingSse = vi.fn();
const closeMock = vi.fn();

vi.mock("../../../src/api/sse", () => ({
  openReconnectingSse: (...args: unknown[]) => openReconnectingSse(...args),
}));

function wrapper(qc: QueryClient) {
  return function Wrap({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
  };
}

describe("useWowReadinessSseInvalidation", () => {
  const tokens = { get: () => "tok" } as AccessTokenStore;

  beforeEach(() => {
    openReconnectingSse.mockReset();
    closeMock.mockReset();
    openReconnectingSse.mockReturnValue({ close: closeMock, closed: false });
  });

  it("subscribes to /api/v1/events while entities are not ready", () => {
    const qc = new QueryClient();
    const invalidateSpy = vi.spyOn(qc, "invalidateQueries");
    renderHook(() => useWowReadinessSseInvalidation(tokens, false), {
      wrapper: wrapper(qc),
    });
    expect(openReconnectingSse).toHaveBeenCalledWith(
      expect.objectContaining({ url: "/api/v1/events", tokens }),
    );
    const onMessage = openReconnectingSse.mock.calls[0]![0].onMessage;
    onMessage({ id: "1", event: "wow_readiness_changed", data: "{}" });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["me", "wow-state"] });
  });

  it("does not subscribe when entities_ready", () => {
    const qc = new QueryClient();
    renderHook(() => useWowReadinessSseInvalidation(tokens, true), {
      wrapper: wrapper(qc),
    });
    expect(openReconnectingSse).not.toHaveBeenCalled();
  });

  it("closes SSE on unmount", () => {
    const qc = new QueryClient();
    const { unmount } = renderHook(() => useWowReadinessSseInvalidation(tokens, false), {
      wrapper: wrapper(qc),
    });
    unmount();
    expect(closeMock).toHaveBeenCalled();
  });
});
