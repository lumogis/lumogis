// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-648 — the /email live-refresh hook invalidates both email caches on
// needs_you / morning_brief SSE events and ignores unrelated events.

import type { ReactNode } from "react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { renderHook } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import type { AccessTokenStore } from "../../../src/api/tokens";
import {
  EMAIL_TRIAGED_KEY,
  EMAIL_WINDOWED_KEY,
  useEmailLiveUpdates,
} from "../../../src/features/email/useEmailLiveUpdates";

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

describe("useEmailLiveUpdates", () => {
  const tokens = { get: () => "tok" } as AccessTokenStore;

  beforeEach(() => {
    openReconnectingSse.mockReset();
    closeMock.mockReset();
    openReconnectingSse.mockReturnValue({ close: closeMock, closed: false });
  });

  it("subscribes to /api/v1/events", () => {
    const qc = new QueryClient();
    renderHook(() => useEmailLiveUpdates(tokens), { wrapper: wrapper(qc) });
    expect(openReconnectingSse).toHaveBeenCalledWith(
      expect.objectContaining({ url: "/api/v1/events", tokens }),
    );
  });

  it.each(["needs_you", "morning_brief"])(
    "invalidates both email caches on %s",
    (event) => {
      const qc = new QueryClient();
      const invalidateSpy = vi.spyOn(qc, "invalidateQueries");
      renderHook(() => useEmailLiveUpdates(tokens), { wrapper: wrapper(qc) });
      const onMessage = openReconnectingSse.mock.calls[0]![0].onMessage;
      onMessage({ id: "1", event, data: "{}" });
      expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: EMAIL_WINDOWED_KEY });
      expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: EMAIL_TRIAGED_KEY });
    },
  );

  it("ignores unrelated SSE events", () => {
    const qc = new QueryClient();
    const invalidateSpy = vi.spyOn(qc, "invalidateQueries");
    renderHook(() => useEmailLiveUpdates(tokens), { wrapper: wrapper(qc) });
    const onMessage = openReconnectingSse.mock.calls[0]![0].onMessage;
    onMessage({ id: "2", event: "signal_received", data: "{}" });
    expect(invalidateSpy).not.toHaveBeenCalled();
  });

  it("closes SSE on unmount", () => {
    const qc = new QueryClient();
    const { unmount } = renderHook(() => useEmailLiveUpdates(tokens), {
      wrapper: wrapper(qc),
    });
    unmount();
    expect(closeMock).toHaveBeenCalled();
  });
});
