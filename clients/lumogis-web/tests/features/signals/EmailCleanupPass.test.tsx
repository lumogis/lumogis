// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
// LUM-646 — EmailCleanupPass: start a date-range pass, poll progress, approve moving
// categories into folders, undo, and the separate spam-folder empty (trash-gated,
// disabled while a job is active).
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import { ApiClient } from "../../../src/api/client";
import { AccessTokenStore } from "../../../src/api/tokens";
import { AuthProvider } from "../../../src/auth/AuthProvider";
import { EmailCleanupPass } from "../../../src/features/signals/EmailCleanupPass";
import { jsonResponse } from "../../helpers/jsonResponse";
import { seedSignedInDevice } from "../../helpers/signedInDevice";

// LUM-886: this suite mounts a real AuthProvider; mark the device as already reconciled so the
// first-sign-in wipe does not remove the view's own queries.
beforeEach(() => {
  seedSignedInDevice("u1");
});

const READY_JOB = {
  state: "ready",
  kind: "window",
  progress: 1,
  window: { start: "2024-01-01", end: "2024-01-31" },
  counts: { scanned: 40, classified: 40, labeled: 40, executed: 0 },
  proposals: [
    { category: "promotional_bulk", label: "Promotional / bulk", count: 12, label_visible: true, can_move: true },
    { category: "bills_finance", label: "Bills & finance", count: 3, label_visible: true, can_move: true },
  ],
};

const BACKFILLING_JOB = { state: "backfilling", kind: "window", progress: 0.4, counts: { scanned: 20, classified: 20, labeled: 20, executed: 0 }, proposals: [] };

function renderView(fetchImpl: typeof fetch, labelVisible = true) {
  const store = new AccessTokenStore();
  const client = new ApiClient({ tokens: store, fetchImpl });
  return render(
    <AuthProvider client={client} tokens={store} skipRefreshOnMount>
      <EmailCleanupPass sourceId="src-1" labelVisible={labelVisible} />
    </AuthProvider>,
  );
}

interface MockOpts {
  job?: unknown;
  spam?: { spam_count: number; trash_available: boolean };
  onApprove?: (body: unknown) => void;
  onUndo?: () => void;
  onStartSpam?: () => void;
}

function makeFetch(opts: MockOpts = {}) {
  const job = opts.job ?? BACKFILLING_JOB;
  const spam = opts.spam ?? { spam_count: 0, trash_available: false };
  return vi.fn(async (input: RequestInfo, init?: RequestInit) => {
    const u = String(input);
    const method = init?.method ?? "GET";
    if (u.includes("/auth/me")) return jsonResponse(200, { id: "u1", email: "u@home.lan", role: "user" });
    if (u.includes("/cleanup-spam")) {
      if (method === "POST") {
        opts.onStartSpam?.();
        return jsonResponse(202, { job_id: "spam-1", state: "backfilling" });
      }
      return jsonResponse(200, spam);
    }
    if (u.includes("/approve") && method === "POST") {
      opts.onApprove?.(JSON.parse(String(init?.body)));
      return jsonResponse(202, { approved: 1 });
    }
    if (u.includes("/undo") && method === "POST") {
      opts.onUndo?.();
      return jsonResponse(202, { undo_pending: true });
    }
    if (/\/cleanup\/[^/]+$/.test(u) && method === "GET") return jsonResponse(200, job);
    if (u.endsWith("/cleanup") && method === "POST") return jsonResponse(202, { job_id: "job-1", state: "backfilling" });
    return jsonResponse(404, {});
  }) as unknown as typeof fetch;
}

describe("EmailCleanupPass", () => {
  let originalFetch: typeof fetch;
  beforeEach(() => {
    originalFetch = globalThis.fetch;
  });
  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it("starts a cleanup pass and shows progress", async () => {
    renderView(makeFetch());
    fireEvent.click(screen.getByText("Start cleanup"));
    await waitFor(() => expect(screen.getByTestId("cleanup-progress")).toBeInTheDocument());
    expect(screen.getByRole("status")).toHaveTextContent(/date range/i);
  });

  it("shows proposals for a ready job and approves the selected category", async () => {
    let approved: unknown = null;
    renderView(makeFetch({ job: READY_JOB, onApprove: (b) => (approved = b) }));
    fireEvent.click(screen.getByText("Start cleanup"));
    // Proposal checkboxes render once the (mocked) job resolves to ready.
    const promo = await screen.findByRole("checkbox", { name: /Promotional/ });
    fireEvent.click(promo);
    fireEvent.click(screen.getByText("File selected into folders"));
    await waitFor(() => expect(approved).not.toBeNull());
    expect((approved as { actions: { category: string }[] }).actions).toEqual([
      { category: "promotional_bulk", move: true },
    ]);
  });

  it("undo posts the reversal", async () => {
    let undone = false;
    renderView(makeFetch({ job: READY_JOB, onUndo: () => (undone = true) }));
    fireEvent.click(screen.getByText("Start cleanup"));
    fireEvent.click(await screen.findByText("Undo this cleanup"));
    await waitFor(() => expect(undone).toBe(true));
  });

  it("offers empty-spam when trash is available and disables it while a job is active", async () => {
    let spammed = false;
    renderView(
      makeFetch({
        job: BACKFILLING_JOB,
        spam: { spam_count: 8, trash_available: true },
        onStartSpam: () => (spammed = true),
      }),
    );
    // Spam status is lazy — check it first (avoids an eager IMAP hit on page load).
    fireEvent.click(screen.getByText("Check spam folder"));
    // Before any window job: the spam button is enabled.
    const spamBtn = await screen.findByText("Empty spam folder → Trash");
    expect(spamBtn).toBeEnabled();
    // Start a window job → it becomes active → spam button disabled (mutual exclusion).
    fireEvent.click(screen.getByText("Start cleanup"));
    await waitFor(() => expect(screen.getByText("Empty spam folder → Trash")).toBeDisabled());
    expect(spammed).toBe(false);
  });
});
