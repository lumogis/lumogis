// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis

import { type ReactNode } from "react";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { DocumentUploadPanel } from "../../../src/features/documents/DocumentUploadPanel";

const uploadMock = vi.fn();
const batchMock = vi.fn();

vi.mock("../../../src/api/ingest", () => ({
  uploadIngestFile: (...args: unknown[]) => uploadMock(...args),
  getIngestBatch: (...args: unknown[]) => batchMock(...args),
  getIngestJob: vi.fn().mockResolvedValue({
    job_id: 1,
    stage: "queued",
    progress_pct: 0,
    status: "pending",
    file_id: "f1",
    batch_id: "b1",
    status_message: null,
    error: null,
    enqueued_at: null,
    started_at: null,
    finished_at: null,
  }),
}));

function wrapper(children: ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

describe("DocumentUploadPanel", () => {
  beforeEach(() => {
    uploadMock.mockReset();
    batchMock.mockReset();
    batchMock.mockResolvedValue({
      batch_id: "batch-1",
      completed: 0,
      failed: 0,
      in_progress: 2,
    });
  });

  it("uploads multiple files sequentially and shows batch counter", async () => {
    uploadMock
      .mockResolvedValueOnce({ status: "queued", file_id: "a", job_id: 1 })
      .mockResolvedValueOnce({ status: "queued", file_id: "b", job_id: 2 });

    const user = userEvent.setup();
    render(wrapper(<DocumentUploadPanel client={{} as never} />));

    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    const f1 = new File(["one"], "one.txt", { type: "text/plain" });
    const f2 = new File(["two"], "two.txt", { type: "text/plain" });
    await user.upload(input, [f1, f2]);

    await waitFor(() => {
      expect(uploadMock).toHaveBeenCalledTimes(2);
    });
    expect(screen.getByTestId("ingest-batch-counter").textContent).toMatch(/of 2/);
  });
});
