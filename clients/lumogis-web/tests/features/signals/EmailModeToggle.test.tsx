// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
// LUM-639 — EmailSignalsView mailbox mode toggle: renders the current mode + the
// pending-operator-enable hint, and toggling calls updateSource (LUM-628 route).
import { type ReactNode } from "react";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const mocks = vi.hoisted(() => ({
  listSources: vi.fn(),
  listSignals: vi.fn().mockResolvedValue({ signals: [], total: 0 }),
  syncSourceNow: vi.fn(),
  updateSource: vi.fn(),
}));

vi.mock("../../../src/api/emailSources", () => mocks);
vi.mock("../../../src/api/meEmailLabels", () => ({
  getTaxonomy: vi.fn().mockResolvedValue({ categories: [] }),
}));
vi.mock("../../../src/auth/AuthProvider", () => ({
  useAuth: () => ({ client: {} }),
}));
vi.mock("../../../src/features/signals/EmailMailboxSetupForm", () => ({
  EmailMailboxSetupForm: () => null,
}));
vi.mock("../../../src/features/signals/SignalListItem", () => ({
  SignalListItem: () => null,
}));

import { EmailSignalsView } from "../../../src/features/signals/EmailSignalsView";

function source(overrides: Record<string, unknown> = {}) {
  return {
    id: "src-1",
    name: "Personal",
    source_type: "imap",
    url: "imap://imap.example.com:993/personal",
    category: "email",
    active: true,
    poll_interval: 900,
    extraction_method: "imap_metadata",
    full_ingest_active: false,
    last_polled_at: null,
    last_signal_at: null,
    polling_active: true,
    ...overrides,
  };
}

function renderView() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  // LUM-849: EmailSignalsView now renders a <Link> to the autonomy page, which
  // needs a router context.
  const ui: ReactNode = (
    <MemoryRouter>
      <QueryClientProvider client={qc}>
        <EmailSignalsView />
      </QueryClientProvider>
    </MemoryRouter>
  );
  return render(ui);
}

afterEach(() => {
  vi.clearAllMocks();
  mocks.listSignals.mockResolvedValue({ signals: [], total: 0 });
});

describe("EmailSignalsView mode toggle", () => {
  it("renders metadata mode with a switch-to-full-ingest button and no pending hint", async () => {
    mocks.listSources.mockResolvedValue({ sources: [source()], total: 1 });
    renderView();
    const mode = await screen.findByTestId("mode-src-1");
    expect(mode.textContent).toContain("Metadata");
    expect(mode.textContent).not.toContain("pending");
    expect(screen.getByRole("button", { name: "Switch Personal to full ingest" })).toBeTruthy();
  });

  it("renders full-ingest active mode with no pending hint", async () => {
    mocks.listSources.mockResolvedValue({
      sources: [source({ extraction_method: "imap_full_ingest", full_ingest_active: true })],
      total: 1,
    });
    renderView();
    const mode = await screen.findByTestId("mode-src-1");
    expect(mode.textContent).toContain("Full ingest");
    expect(mode.textContent).not.toContain("pending");
    expect(screen.getByRole("button", { name: "Switch Personal to metadata" })).toBeTruthy();
  });

  it("shows the pending-operator-enable hint when full-ingest but the flag is off", async () => {
    mocks.listSources.mockResolvedValue({
      sources: [source({ extraction_method: "imap_full_ingest", full_ingest_active: false })],
      total: 1,
    });
    renderView();
    const mode = await screen.findByTestId("mode-src-1");
    expect(mode.textContent).toContain("Full ingest");
    expect(mode.textContent).toContain("pending operator enable");
  });

  it("toggling calls updateSource with the flipped mode and shows a status", async () => {
    mocks.listSources.mockResolvedValue({ sources: [source()], total: 1 });
    mocks.updateSource.mockResolvedValue({
      status: "updated",
      source_id: "src-1",
      source_type: "imap",
      extraction_method: "imap_full_ingest",
      full_ingest_active: false,
      rescheduled: true,
    });
    renderView();
    const btn = await screen.findByRole("button", { name: "Switch Personal to full ingest" });
    await userEvent.click(btn);
    expect(mocks.updateSource).toHaveBeenCalledWith(expect.anything(), "src-1", {
      full_ingest: true,
    });
    // LUM-769: `findByRole("status")` is ambiguous now — MailboxSendControl
    // renders one live region per mailbox. Wait on the message itself and
    // assert its role, which is what this line was actually for.
    expect(await screen.findByTestId("mode-msg")).toHaveAttribute("role", "status");
    expect(screen.getByTestId("mode-msg").textContent).toContain("pending operator enable");
  });

  it("shows an error message when the toggle fails (no raw detail)", async () => {
    mocks.listSources.mockResolvedValue({ sources: [source()], total: 1 });
    mocks.updateSource.mockRejectedValue(new Error("boom: postgres://secret@host"));
    renderView();
    const btn = await screen.findByRole("button", { name: "Switch Personal to full ingest" });
    await userEvent.click(btn);
    await waitFor(() => {
      expect(screen.getByTestId("mode-msg").textContent).toContain("Couldn't change mode");
    });
    expect(
      screen
        .getAllByRole("status")
        .map((el) => el.textContent ?? "")
        .join(" "),
    ).not.toContain("secret");
  });
});
