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
import { toast } from "sonner";

const mocks = vi.hoisted(() => ({
  listSources: vi.fn(),
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
// Success messages are sonner toasts (LUM-993); there is no Toaster in this tree.
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

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
});

/** Open the card's "…" menu and choose an item. */
async function chooseFromMenu(name: string) {
  await userEvent.click(await screen.findByRole("button", { name: "More actions for Personal" }));
  await userEvent.click(await screen.findByRole("menuitem", { name }));
}

describe("EmailSignalsView mode toggle", () => {
  it("renders subject-and-sender mode with a switch-to-full-messages item and no pending hint", async () => {
    mocks.listSources.mockResolvedValue({ sources: [source()], total: 1 });
    renderView();
    const mode = await screen.findByTestId("mode-src-1");
    expect(mode.textContent).toContain("Subject and sender only");
    expect(mode.textContent).not.toContain("waiting for");
    await userEvent.click(screen.getByRole("button", { name: "More actions for Personal" }));
    expect(await screen.findByRole("menuitem", { name: "Switch to full messages" })).toBeTruthy();
  });

  it("renders full-messages active mode with no pending hint", async () => {
    mocks.listSources.mockResolvedValue({
      sources: [source({ extraction_method: "imap_full_ingest", full_ingest_active: true })],
      total: 1,
    });
    renderView();
    const mode = await screen.findByTestId("mode-src-1");
    expect(mode.textContent).toContain("Full messages");
    expect(mode.textContent).not.toContain("waiting for");
    await userEvent.click(screen.getByRole("button", { name: "More actions for Personal" }));
    expect(
      await screen.findByRole("menuitem", { name: "Switch to subject and sender only" }),
    ).toBeTruthy();
  });

  it("shows the waiting-for-admin hint when full messages is chosen but the flag is off", async () => {
    mocks.listSources.mockResolvedValue({
      sources: [source({ extraction_method: "imap_full_ingest", full_ingest_active: false })],
      total: 1,
    });
    renderView();
    const mode = await screen.findByTestId("mode-src-1");
    expect(mode.textContent).toContain("Full messages");
    expect(mode.textContent).toContain("waiting for the household admin to allow it");
  });

  it("toggling calls updateSource with the flipped mode and toasts the result", async () => {
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
    await chooseFromMenu("Switch to full messages");
    await waitFor(() =>
      expect(mocks.updateSource).toHaveBeenCalledWith(expect.anything(), "src-1", {
        full_ingest: true,
      }),
    );
    await waitFor(() =>
      expect(toast.success).toHaveBeenCalledWith(
        "Switched to full messages (waiting for the household admin to allow it).",
      ),
    );
  });

  it("shows an inline error when the toggle fails (no raw detail)", async () => {
    mocks.listSources.mockResolvedValue({ sources: [source()], total: 1 });
    mocks.updateSource.mockRejectedValue(new Error("boom: postgres://secret@host"));
    renderView();
    await chooseFromMenu("Switch to full messages");
    await waitFor(() => {
      expect(screen.getByTestId("mode-msg").textContent).toContain("Couldn't change mode");
    });
    expect(screen.getByTestId("mode-msg")).toHaveAttribute("role", "alert");
    expect(screen.getByTestId("mode-msg").textContent).not.toContain("secret");
  });
});
