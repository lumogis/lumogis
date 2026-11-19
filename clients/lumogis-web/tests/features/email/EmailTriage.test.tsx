// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
// LUM-643 — triage mutations on the /email surface: Done moves a card to the Done
// lane and PATCHes; Snooze moves it into the Snoozed disclosure; Reopen clears;
// a failed mutation reverts the optimistic update and shows a retry banner.
import { type ReactNode } from "react";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";

const mocks = vi.hoisted(() => ({
  listSources: vi.fn(),
  listSignals: vi.fn(),
  setTriage: vi.fn(),
  clearTriage: vi.fn(),
}));
vi.mock("../../../src/api/emailSources", () => mocks);
vi.mock("../../../src/auth/AuthProvider", () => ({ useAuth: () => ({ client: {} }) }));

import { EmailInboxView } from "../../../src/features/email/EmailInboxView";

function imapSource(id: string) {
  return {
    id, name: `Mailbox ${id}`, source_type: "imap", url: `imap://h/${id}`,
    category: "email", active: true, poll_interval: 900,
    extraction_method: "imap_metadata", full_ingest_active: false,
    last_polled_at: "2026-07-20T10:00:00Z", last_signal_at: null, polling_active: true,
  };
}
function sig(overrides: Record<string, unknown> = {}) {
  return {
    signal_id: "s1", source_id: "src-A", title: "Renew passport", url: "imap://src-A/1/1",
    category: "", category_label: "", scope: "personal", published_at: "2026-07-20T09:00:00Z",
    content_summary: "", importance_score: 0.9, ...overrides,
  };
}

/** Stateful signals mock: windowed fetch always returns the signal (with whatever
 *  triage_status `state` currently holds); the triaged fetch returns it only once
 *  it carries a triage row — mirroring the backend's two queries. */
function wireSignals(state: { triage_status?: string | null; snoozed_until?: string | null }) {
  mocks.listSources.mockResolvedValue({ sources: [imapSource("src-A")], total: 1 });
  mocks.listSignals.mockImplementation((_client: unknown, params?: { triaged?: string }) => {
    const s = sig({ triage_status: state.triage_status ?? undefined, snoozed_until: state.snoozed_until ?? null });
    if (params?.triaged) {
      return Promise.resolve({ signals: state.triage_status ? [s] : [], total: state.triage_status ? 1 : 0 });
    }
    return Promise.resolve({ signals: [s], total: 1 });
  });
}

function renderView() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const ui: ReactNode = (
    <QueryClientProvider client={qc}>
      <MemoryRouter><EmailInboxView /></MemoryRouter>
    </QueryClientProvider>
  );
  return render(ui);
}

afterEach(() => vi.clearAllMocks());

describe("EmailInboxView triage mutations", () => {
  it("marking Done PATCHes {status:'done'} and moves the card to the Done lane", async () => {
    const state: { triage_status?: string | null } = {};
    wireSignals(state);
    mocks.setTriage.mockImplementation(async () => {
      state.triage_status = "done";
      return { signal_id: "s1", status: "done", snoozed_until: null, updated_at: "2026-07-21T12:00:00Z" };
    });
    renderView();

    await waitFor(() => expect(screen.getByText("Renew passport")).toBeInTheDocument());
    // starts in Needs you (importance 0.9, untriaged).
    expect(within(screen.getByTestId("lane-needs_you")).getByText("Renew passport")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Done" }));

    expect(mocks.setTriage).toHaveBeenCalledWith(expect.anything(), "s1", "done", undefined);
    await waitFor(() =>
      expect(within(screen.getByTestId("lane-done")).getByText("Renew passport")).toBeInTheDocument(),
    );
  });

  it("Snooze preset PATCHes {status:'snoozed', snoozed_until} and hides the card in the Snoozed disclosure", async () => {
    const state: { triage_status?: string | null; snoozed_until?: string | null } = {};
    wireSignals(state);
    mocks.setTriage.mockImplementation(async (_c: unknown, _id: string, _status: string, until?: string) => {
      state.triage_status = "snoozed";
      state.snoozed_until = until ?? null;
      return { signal_id: "s1", status: "snoozed", snoozed_until: until ?? null, updated_at: "2026-07-21T12:00:00Z" };
    });
    renderView();

    await waitFor(() => expect(screen.getByText("Renew passport")).toBeInTheDocument());
    await userEvent.click(screen.getByRole("button", { name: "Snooze" }));
    await userEvent.click(await screen.findByRole("menuitem", { name: "Tomorrow" }));

    expect(mocks.setTriage).toHaveBeenCalledWith(
      expect.anything(), "s1", "snoozed", expect.stringMatching(/^\d{4}-\d{2}-\d{2}T/),
    );
    await waitFor(() => {
      const disclosure = screen.getByTestId("snoozed-disclosure");
      expect(within(disclosure).getByText("Renew passport")).toBeInTheDocument();
    });
    // and it's out of the three lanes.
    expect(within(screen.getByTestId("lane-needs_you")).queryByText("Renew passport")).not.toBeInTheDocument();
  });

  it("Reopen clears the triage row → card returns to its default lane", async () => {
    const state: { triage_status?: string | null } = { triage_status: "done" };
    wireSignals(state);
    mocks.clearTriage.mockImplementation(async () => {
      state.triage_status = null;
    });
    renderView();

    await waitFor(() =>
      expect(within(screen.getByTestId("lane-done")).getByText("Renew passport")).toBeInTheDocument(),
    );
    await userEvent.click(screen.getByRole("button", { name: "Reopen" }));

    expect(mocks.clearTriage).toHaveBeenCalledWith(expect.anything(), "s1");
    await waitFor(() =>
      expect(within(screen.getByTestId("lane-needs_you")).getByText("Renew passport")).toBeInTheDocument(),
    );
  });

  it("reopening an item that lives only in the triaged fetch removes it (no flash into a lane)", async () => {
    // LUM-660: an old done email older than the recency window is returned by the
    // triaged fetch but NOT the windowed one. Reopening it must drop it (old +
    // untriaged = not shown), never flash it into a lane and then vanish.
    mocks.listSources.mockResolvedValue({ sources: [imapSource("src-A")], total: 1 });
    const state = { cleared: false };
    mocks.listSignals.mockImplementation((_c: unknown, params?: { triaged?: string }) => {
      if (params?.triaged) {
        // triaged fetch: the old done item, until it's cleared.
        return Promise.resolve(
          state.cleared
            ? { signals: [], total: 0 }
            : { signals: [sig({ signal_id: "old", title: "Old receipt", triage_status: "done" })], total: 1 },
        );
      }
      // windowed fetch never contains the old item.
      return Promise.resolve({ signals: [], total: 0 });
    });
    mocks.clearTriage.mockImplementation(async () => {
      state.cleared = true;
    });
    renderView();

    await waitFor(() =>
      expect(within(screen.getByTestId("lane-done")).getByText("Old receipt")).toBeInTheDocument(),
    );
    await userEvent.click(screen.getByRole("button", { name: "Reopen" }));

    expect(mocks.clearTriage).toHaveBeenCalledWith(expect.anything(), "old");
    // It leaves entirely — never appears in Needs you / Good to know.
    await waitFor(() => expect(screen.queryByText("Old receipt")).not.toBeInTheDocument());
    expect(within(screen.getByTestId("lane-needs_you")).queryByText("Old receipt")).not.toBeInTheDocument();
  });

  it("loads older triaged items with 'Show older' (keyset pagination, LUM-659)", async () => {
    mocks.listSources.mockResolvedValue({ sources: [imapSource("src-A")], total: 1 });
    mocks.listSignals.mockImplementation((_c: unknown, params?: { triaged?: string; triagedBefore?: string }) => {
      if (!params?.triaged) return Promise.resolve({ signals: [], total: 0 }); // windowed empty
      if (!params.triagedBefore) {
        // page 1 — a full page hands back a cursor.
        return Promise.resolve({
          signals: [sig({ signal_id: "p1", title: "Newer done", triage_status: "done", triage_updated_at: "2026-07-20T00:00:00+00:00" })],
          total: 1,
          next_before: "2026-07-20T00:00:00+00:00",
        });
      }
      // page 2 — older item, no further pages.
      return Promise.resolve({
        signals: [sig({ signal_id: "p2", title: "Older done", triage_status: "done", triage_updated_at: "2026-07-01T00:00:00+00:00" })],
        total: 1,
        next_before: null,
      });
    });
    renderView();

    await waitFor(() =>
      expect(within(screen.getByTestId("lane-done")).getByText("Newer done")).toBeInTheDocument(),
    );
    expect(screen.queryByText("Older done")).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /Show older/ }));

    await waitFor(() =>
      expect(within(screen.getByTestId("lane-done")).getByText("Older done")).toBeInTheDocument(),
    );
    // Newer page stays visible, and the button disappears (no more pages).
    expect(within(screen.getByTestId("lane-done")).getByText("Newer done")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Show older/ })).not.toBeInTheDocument();
  });

  it("a failed mutation reverts the optimistic move and shows a retry banner", async () => {
    const state: { triage_status?: string | null } = {};
    wireSignals(state);
    mocks.setTriage.mockRejectedValue(new Error("boom")); // never mutates state
    renderView();

    await waitFor(() => expect(screen.getByText("Renew passport")).toBeInTheDocument());
    await userEvent.click(screen.getByRole("button", { name: "Done" }));

    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("Couldn't update — retry"));
    // reverted: card is back in Needs you, NOT in Done.
    expect(within(screen.getByTestId("lane-needs_you")).getByText("Renew passport")).toBeInTheDocument();
    expect(within(screen.getByTestId("lane-done")).queryByText("Renew passport")).not.toBeInTheDocument();
  });
});
