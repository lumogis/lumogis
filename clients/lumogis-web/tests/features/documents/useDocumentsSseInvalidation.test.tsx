// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis

import type { ReactNode } from "react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { renderHook } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import type { AccessTokenStore } from "../../../src/api/tokens";
import { useDocumentsSseInvalidation } from "../../../src/features/documents/useDocumentsSseInvalidation";
import { documentsQueryKey } from "../../../src/features/documents/useDocuments";
import { ingestJobQueryKey } from "../../../src/features/documents/useIngestJobProgress";

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

describe("useDocumentsSseInvalidation", () => {
  const tokens = { get: () => "tok" } as AccessTokenStore;

  beforeEach(() => {
    openReconnectingSse.mockReset();
    closeMock.mockReset();
    openReconnectingSse.mockReturnValue({ close: closeMock, closed: false });
  });

  it("subscribes to /api/v1/events", () => {
    const qc = new QueryClient();
    renderHook(() => useDocumentsSseInvalidation(tokens), { wrapper: wrapper(qc) });
    expect(openReconnectingSse).toHaveBeenCalledWith(
      expect.objectContaining({ url: "/api/v1/events", tokens }),
    );
  });

  it("invalidates documents on document_status_changed", () => {
    const qc = new QueryClient();
    const invalidateSpy = vi.spyOn(qc, "invalidateQueries");
    renderHook(() => useDocumentsSseInvalidation(tokens), { wrapper: wrapper(qc) });
    const onMessage = openReconnectingSse.mock.calls[0]![0].onMessage;
    onMessage({ id: "1", event: "document_status_changed", data: "{}" });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: documentsQueryKey });
  });

  it("invalidates documents and ingest job on ingest_progress with job_id", () => {
    const qc = new QueryClient();
    const invalidateSpy = vi.spyOn(qc, "invalidateQueries");
    renderHook(() => useDocumentsSseInvalidation(tokens), { wrapper: wrapper(qc) });
    const onMessage = openReconnectingSse.mock.calls[0]![0].onMessage;
    onMessage({
      id: "2",
      event: "ingest_progress",
      data: JSON.stringify({ job_id: 42, stage: "embedding" }),
    });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: documentsQueryKey });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ingestJobQueryKey(42) });
  });

  it("invalidates documents only when ingest_progress payload is malformed", () => {
    const qc = new QueryClient();
    const invalidateSpy = vi.spyOn(qc, "invalidateQueries");
    renderHook(() => useDocumentsSseInvalidation(tokens), { wrapper: wrapper(qc) });
    const onMessage = openReconnectingSse.mock.calls[0]![0].onMessage;
    onMessage({ id: "3", event: "ingest_progress", data: "not-json" });
    expect(invalidateSpy).toHaveBeenCalledTimes(1);
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: documentsQueryKey });
  });

  it("closes SSE on unmount", () => {
    const qc = new QueryClient();
    const { unmount } = renderHook(() => useDocumentsSseInvalidation(tokens), {
      wrapper: wrapper(qc),
    });
    unmount();
    expect(closeMock).toHaveBeenCalled();
  });
});
