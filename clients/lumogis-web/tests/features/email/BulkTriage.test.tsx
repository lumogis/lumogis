// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-696 — multi-select and bulk triage over the real EmailInboxView.
//
// Three things here are worth more than the rest:
//   * a bulk action must be ONE request, not N (the whole reason the route exists);
//   * a partial chunk failure must keep what landed — reverting it would tell the
//     user nothing happened when 200 things did;
//   * undo must restore each row's OWN prior state, which is what a naive
//     "undo = clear everything" gets wrong.

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
  bulkTriage: vi.fn(),
  MAX_BULK_TRIAGE: 200,
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

function sig(id: string, overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    signal_id: id,
    source_id: "src-A",
    title: `Message ${id}`,
    url: `imap://src-A/1/${id}`,
    category: "",
    category_label: "",
    scope: "personal",
    published_at: "2026-07-20T09:00:00Z",
    content_summary: "",
    from_addr: "someone@example.com",
    from_display: "Someone",
    importance_score: 0.9,
    ...overrides,
  };
}

/** Wire N untriaged signals into the windowed fetch. */
function wire(signals: Record<string, unknown>[]) {
  mocks.listSources.mockResolvedValue({ sources: [imapSource("src-A")], total: 1 });
  mocks.listSignals.mockImplementation((_c: unknown, params?: { triaged?: string }) => {
    if (params?.triaged) {
      const triaged = signals.filter((s) => s.triage_status);
      return Promise.resolve({ signals: triaged, total: triaged.length });
    }
    return Promise.resolve({ signals, total: signals.length });
  });
  mocks.bulkTriage.mockResolvedValue({ updated: [], skipped: [], count: 0 });
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

const checkboxFor = (id: string) =>
  document.querySelector<HTMLInputElement>(`[data-triage-checkbox="${id}"]`)!;

afterEach(() => vi.clearAllMocks());

describe("selection", () => {
  it("selecting two rows and pressing Mark done sends ONE request", async () => {
    wire([sig("a"), sig("b"), sig("c")]);
    const user = userEvent.setup();
    renderView();
    await screen.findByTestId("email-card-a");

    await user.click(checkboxFor("a"));
    await user.click(checkboxFor("b"));
    await user.click(within(screen.getByTestId("bulk-action-bar")).getByText("Mark done"));

    await waitFor(() => expect(mocks.bulkTriage).toHaveBeenCalledTimes(1));
    expect(mocks.bulkTriage.mock.calls[0][1].sort()).toEqual(["a", "b"]);
    expect(mocks.bulkTriage.mock.calls[0][2]).toBe("done");
    // The single-row path must NOT be used — that is the fan-out this replaces.
    expect(mocks.setTriage).not.toHaveBeenCalled();
  });

  it("the bulk bar is absent until something is selected", async () => {
    wire([sig("a")]);
    renderView();
    await screen.findByTestId("email-card-a");
    expect(screen.queryByTestId("bulk-action-bar")).toBeNull();
  });

  it("shift-clicking selects the inclusive range", async () => {
    wire([sig("a"), sig("b"), sig("c"), sig("d")]);
    const user = userEvent.setup();
    renderView();
    await screen.findByTestId("email-card-a");

    await user.click(checkboxFor("a"));
    // userEvent ignores a `{shiftKey}` options arg on click — the modifier has
    // to be genuinely held down. (Flagged as an unverified assumption in the
    // plan's self-review; it was wrong, and this is what actually works.)
    await user.keyboard("{Shift>}");
    await user.click(checkboxFor("c"));
    await user.keyboard("{/Shift}");

    expect(screen.getByTestId("bulk-action-bar")).toHaveTextContent("3 selected");
  });

  it("the lane select-all checkbox selects that lane's rows", async () => {
    wire([sig("a"), sig("b")]);
    const user = userEvent.setup();
    renderView();
    await screen.findByTestId("email-card-a");

    await user.click(screen.getByTestId("select-all-needs_you"));

    expect(screen.getByTestId("bulk-action-bar")).toHaveTextContent("2 selected");
  });

  it("filtering away a selected row removes it from the selection", async () => {
    wire([sig("a", { title: "Passport renewal" }), sig("b", { title: "Gas bill" })]);
    const user = userEvent.setup();
    renderView();
    await screen.findByTestId("email-card-a");

    await user.click(checkboxFor("a"));
    await user.click(checkboxFor("b"));
    expect(screen.getByTestId("bulk-action-bar")).toHaveTextContent("2 selected");

    // Acting on rows the user can no longer see is the failure mode a bulk bar
    // must not have.
    await user.type(screen.getByLabelText("Filter loaded email"), "Passport");

    await waitFor(() =>
      expect(screen.getByTestId("bulk-action-bar")).toHaveTextContent("1 selected"),
    );
  });

  it("Clear selection issues no request", async () => {
    wire([sig("a")]);
    const user = userEvent.setup();
    renderView();
    await screen.findByTestId("email-card-a");

    await user.click(checkboxFor("a"));
    await user.click(screen.getByText("Clear selection"));

    expect(screen.queryByTestId("bulk-action-bar")).toBeNull();
    expect(mocks.bulkTriage).not.toHaveBeenCalled();
  });
});

describe("chunking and partial failure", () => {
  // Cap + 1 is enough to force two sequential chunks. Using 250 cards made these
  // flake under parallel Vitest workers (5s default timeout while the DOM settles).
  const OVER_CAP = mocks.MAX_BULK_TRIAGE + 1;
  const many = (n: number) => Array.from({ length: n }, (_, i) => sig(`s${i}`));

  it(
    "a selection larger than the cap is sent as sequential chunks",
    async () => {
      wire(many(OVER_CAP));
      const user = userEvent.setup();
      renderView();
      await screen.findByTestId("email-card-s0");

      await user.click(screen.getByTestId("select-all-needs_you"));
      await user.click(within(screen.getByTestId("bulk-action-bar")).getByText("Mark done"));

      await waitFor(() => expect(mocks.bulkTriage).toHaveBeenCalledTimes(2));
      expect(mocks.bulkTriage.mock.calls[0][1]).toHaveLength(mocks.MAX_BULK_TRIAGE);
      expect(mocks.bulkTriage.mock.calls[1][1]).toHaveLength(1);
    },
    15_000,
  );

  it(
    "a chunk failure stops the rest and KEEPS what landed",
    async () => {
      wire(many(OVER_CAP));
      mocks.bulkTriage
        .mockResolvedValueOnce({ updated: [], skipped: [], count: mocks.MAX_BULK_TRIAGE })
        .mockRejectedValueOnce(new Error("network"));
      const user = userEvent.setup();
      renderView();
      await screen.findByTestId("email-card-s0");

      await user.click(screen.getByTestId("select-all-needs_you"));
      await user.click(within(screen.getByTestId("bulk-action-bar")).getByText("Mark done"));

      // No third call, and the banner names how many landed rather than claiming
      // nothing happened.
      await waitFor(() =>
        expect(screen.getByRole("alert")).toHaveTextContent(
          `${mocks.MAX_BULK_TRIAGE} of ${OVER_CAP}`,
        ),
      );
      expect(mocks.bulkTriage).toHaveBeenCalledTimes(2);
      // Exactly the remainder that failed stay selected, so retry retries the failures.
      expect(screen.getByTestId("bulk-action-bar")).toHaveTextContent("1 selected");
    },
    15_000,
  );

  it(
    "a first-chunk failure reverts everything and reports a plain retry",
    async () => {
      wire([sig("a"), sig("b")]);
      mocks.bulkTriage.mockRejectedValueOnce(new Error("network"));
      const user = userEvent.setup();
      renderView();
      await screen.findByTestId("email-card-a");

      await user.click(screen.getByTestId("select-all-needs_you"));
      await user.click(within(screen.getByTestId("bulk-action-bar")).getByText("Mark done"));

      await waitFor(() =>
        expect(screen.getByRole("alert")).toHaveTextContent("Couldn't update"),
      );
      // The selection is preserved so the user can retry without re-selecting.
      expect(screen.getByTestId("bulk-action-bar")).toHaveTextContent("2 selected");
    },
    15_000,
  );
});

describe("the server's skipped list", () => {
  // A 200 with a non-empty `skipped` is NOT an error, so it never reaches
  // onError — but it means rows were optimistically patched that the server did
  // not write. Before this was handled, every test in this file used
  // `skipped: []`, so the whole path was untested.
  it(
    "does not count skipped ids as landed",
    async () => {
      wire([sig("a"), sig("b"), sig("c")]);
      mocks.bulkTriage.mockResolvedValue({ updated: [], skipped: ["c"], count: 2 });
      const user = userEvent.setup();
      renderView();
      await screen.findByTestId("email-card-a");

      await user.click(screen.getByTestId("select-all-needs_you"));
      await user.click(within(screen.getByTestId("bulk-action-bar")).getByText("Mark done"));

      await waitFor(() =>
        expect(screen.getByRole("alert")).toHaveTextContent("2 of 3"),
      );
      expect(screen.getByRole("alert")).toHaveTextContent("no longer available");
    },
    15_000,
  );

  it(
    "keeps skipped ids out of the undo snapshot",
    async () => {
      wire([sig("a"), sig("b")]);
      mocks.bulkTriage.mockResolvedValue({ updated: [], skipped: ["b"], count: 1 });
      const user = userEvent.setup();
      renderView();
      await screen.findByTestId("email-card-a");

      await user.click(screen.getByTestId("select-all-needs_you"));
      await user.click(within(screen.getByTestId("bulk-action-bar")).getByText("Mark done"));
      await screen.findByTestId("undo-banner", {}, { timeout: 10_000 });

      mocks.bulkTriage.mockClear();
      mocks.bulkTriage.mockResolvedValue({ updated: [], skipped: [], count: 1 });
      await user.click(within(screen.getByTestId("undo-banner")).getByText("Undo"));

      // Only `a` is offered for undo — `b` was never changed, so reverting it would
      // be a change the user never made.
      await waitFor(() => expect(mocks.bulkTriage).toHaveBeenCalledTimes(1));
      expect(mocks.bulkTriage.mock.calls[0][1]).toEqual(["a"]);
    },
    15_000,
  );
});

describe("undo", () => {
  it("restores each row to the state it had, not to a single state", async () => {
    // The case a naive "undo = clear everything" gets wrong: `was-done` was
    // already Done before the bulk snooze, and clearing it would be a change the
    // user never asked for and cannot see.
    wire([sig("was-done", { triage_status: "done" }), sig("was-untriaged")]);
    const user = userEvent.setup();
    renderView();
    await screen.findByTestId("email-card-was-untriaged");

    await user.click(checkboxFor("was-untriaged"));
    await user.click(checkboxFor("was-done"));
    await user.click(within(screen.getByTestId("bulk-action-bar")).getByText("Snooze"));
    await user.click(
      within(screen.getByTestId("bulk-snooze-menu")).getByText("Tomorrow"),
    );
    await waitFor(() => expect(mocks.bulkTriage).toHaveBeenCalledTimes(1));

    mocks.bulkTriage.mockClear();
    await user.click(within(screen.getByTestId("undo-banner")).getByText("Undo"));

    // TWO calls: one restoring `done`, one clearing the other.
    await waitFor(() => expect(mocks.bulkTriage).toHaveBeenCalledTimes(2));
    const byStatus = new Map(
      mocks.bulkTriage.mock.calls.map((c: unknown[]) => [c[2], c[1] as string[]]),
    );
    expect(byStatus.get("done")).toEqual(["was-done"]);
    expect(byStatus.get(null)).toEqual(["was-untriaged"]);
  });

  it("the undo banner is dismissable and does not vanish on a timer", async () => {
    wire([sig("a")]);
    const user = userEvent.setup();
    renderView();
    await screen.findByTestId("email-card-a");

    await user.click(checkboxFor("a"));
    await user.click(within(screen.getByTestId("bulk-action-bar")).getByText("Mark done"));
    await screen.findByTestId("undo-banner");

    await user.click(within(screen.getByTestId("undo-banner")).getByLabelText("Dismiss"));
    expect(screen.queryByTestId("undo-banner")).toBeNull();
  });
});

describe("keyboard", () => {
  it("e marks the cursor row done without any selection", async () => {
    wire([sig("a"), sig("b")]);
    const user = userEvent.setup();
    renderView();
    await screen.findByTestId("email-card-a");

    await user.keyboard("j"); // cursor to the first row
    await user.keyboard("e");

    await waitFor(() => expect(mocks.bulkTriage).toHaveBeenCalledTimes(1));
    expect(mocks.bulkTriage.mock.calls[0][1]).toEqual(["a"]);
  });

  it("moving the cursor moves DOM focus to the row's checkbox", async () => {
    wire([sig("a"), sig("b")]);
    const user = userEvent.setup();
    renderView();
    await screen.findByTestId("email-card-a");

    await user.keyboard("j");
    await waitFor(() => expect(document.activeElement).toBe(checkboxFor("a")));
    await user.keyboard("j");
    await waitFor(() => expect(document.activeElement).toBe(checkboxFor("b")));
  });

  it("after e, the cursor takes over the vacated index so the NEXT e still fires", async () => {
    // The normal case during rapid triage, not an edge case: press `e`, the row
    // leaves the lane, and if the cursor is not recovered every subsequent
    // keystroke is a silent no-op — the surface feels broken during exactly the
    // flow this feature exists for. (DoD item 23.)
    const state: Record<string, string | undefined> = {};
    mocks.listSources.mockResolvedValue({ sources: [imapSource("src-A")], total: 1 });
    mocks.listSignals.mockImplementation((_c: unknown, p?: { triaged?: string }) => {
      const rows = [sig("a"), sig("b"), sig("c")].map((s) => ({
        ...s,
        triage_status: state[s.signal_id as string],
      }));
      if (p?.triaged) {
        const tr = rows.filter((s) => s.triage_status);
        return Promise.resolve({ signals: tr, total: tr.length });
      }
      return Promise.resolve({ signals: rows, total: rows.length });
    });
    mocks.bulkTriage.mockImplementation((_c: unknown, ids: string[]) => {
      for (const id of ids) state[id] = "done";
      return Promise.resolve({ updated: [], skipped: [], count: ids.length });
    });

    const user = userEvent.setup();
    renderView();
    await screen.findByTestId("email-card-a");

    await user.keyboard("j"); // cursor onto "a"
    await waitFor(() => expect(document.activeElement).toBe(checkboxFor("a")));
    await user.keyboard("e");
    await waitFor(() => expect(mocks.bulkTriage).toHaveBeenCalledTimes(1));

    // "a" has left Needs you; the cursor must now hold that index — i.e. "b".
    await waitFor(() => expect(document.activeElement).toBe(checkboxFor("b")));

    await user.keyboard("e");
    await waitFor(() => expect(mocks.bulkTriage).toHaveBeenCalledTimes(2));
    expect(mocks.bulkTriage.mock.calls[1][1]).toEqual(["b"]);
  });

  it("typing in the filter box does not trigger shortcuts", async () => {
    wire([sig("a")]);
    const user = userEvent.setup();
    renderView();
    await screen.findByTestId("email-card-a");

    await user.click(screen.getByLabelText("Filter loaded email"));
    await user.keyboard("efjx");

    expect(mocks.bulkTriage).not.toHaveBeenCalled();
    expect(screen.getByLabelText("Filter loaded email")).toHaveValue("efjx");
  });

  it("? opens the help overlay and it lists every bound key", async () => {
    wire([sig("a")]);
    const user = userEvent.setup();
    renderView();
    await screen.findByTestId("email-card-a");

    await user.keyboard("?");

    const help = await screen.findByTestId("shortcuts-help");
    expect(help).toHaveAttribute("role", "dialog");
    for (const k of ["e", "s", "u", "x", "?"]) {
      expect(help.textContent).toContain(k);
    }
  });

  it("shortcuts do not fire while the composer is open", async () => {
    wire([sig("a")]);
    const user = userEvent.setup();
    renderView();
    await screen.findByTestId("email-card-a");

    await user.click(within(screen.getByTestId("email-card-a")).getByText("Reply"));
    await user.keyboard("e");

    expect(mocks.bulkTriage).not.toHaveBeenCalled();
  });
});
