// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
// LUM-642 — the /email destination: three lanes, household-union email scoping
// (incl. cross-member shared email), three route states, filters, search link.
import { type ReactNode } from "react";
import { render, screen, waitFor, waitForElementToBeRemoved } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";

const mocks = vi.hoisted(() => ({
  listSources: vi.fn(),
  listSignals: vi.fn(),
  setTriage: vi.fn(),
  clearTriage: vi.fn(),
  postSignalFeedback: vi.fn(),
  listMyCredentials: vi.fn(),
}));
vi.mock("../../../src/api/emailSources", () => ({
  listSources: mocks.listSources,
  listSignals: mocks.listSignals,
  setTriage: mocks.setTriage,
  clearTriage: mocks.clearTriage,
}));
vi.mock("../../../src/api/relevanceProfile", () => ({
  postSignalFeedback: mocks.postSignalFeedback,
}));
// LUM-830: only the network call is mocked. `hasSmtpCredential` stays REAL —
// it is the predicate under test, and a hand-written copy in a mock would let
// the two drift while this file kept passing.
vi.mock("../../../src/api/connectorCredentials", async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  listMyCredentials: mocks.listMyCredentials,
}));
vi.mock("../../../src/auth/AuthProvider", () => ({ useAuth: () => ({ client: {} }) }));

import { EmailInboxView } from "../../../src/features/email/EmailInboxView";
import { isEmailActivated } from "../../../src/features/email/activation";

describe("isEmailActivated (nav gate)", () => {
  it("true when at least one imap source is configured", () => {
    expect(isEmailActivated([imapSource("src-A") as never])).toBe(true);
  });
  it("false when no imap source (or undefined while loading/error → fail closed)", () => {
    expect(isEmailActivated([])).toBe(false);
    expect(isEmailActivated(undefined)).toBe(false);
    expect(isEmailActivated([{ ...imapSource("x"), source_type: "rss" } as never])).toBe(false);
  });
});

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
    signal_id: "s1", source_id: "src-A", title: "Title", url: "imap://src-A/1/1",
    category: "", category_label: "", scope: "personal", published_at: "2026-07-20T09:00:00Z",
    content_summary: "", ...overrides,
  };
}

function renderView() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const ui: ReactNode = (
    <QueryClientProvider client={qc}>
      <MemoryRouter><EmailInboxView /></MemoryRouter>
    </QueryClientProvider>
  );
  return render(ui);
}

afterEach(() => {
  vi.clearAllMocks();
});

describe("EmailInboxView", () => {
  it("renders lanes and sorts by notified/importance; queries email-only", async () => {
    mocks.listSources.mockResolvedValue({ sources: [imapSource("src-A")], total: 1 });
    mocks.listSignals.mockResolvedValue({
      signals: [
        sig({ signal_id: "needy", title: "Needs you item", notified: true }),
        sig({ signal_id: "fyi", title: "FYI item", notified: false, importance_score: 0.1 }),
      ],
      total: 2,
    });
    renderView();

    await waitFor(() => expect(screen.getByText("Needs you item")).toBeInTheDocument());
    expect(screen.getByText("FYI item")).toBeInTheDocument();
    // email-only scoping is a backend concern → the client asks for it explicitly.
    expect(mocks.listSignals).toHaveBeenCalledWith(expect.anything(), { source_type: "imap", limit: 100 });
    // three lanes are present as a shell.
    expect(screen.getByTestId("lane-needs_you")).toBeInTheDocument();
    expect(screen.getByTestId("lane-good_to_know")).toBeInTheDocument();
    expect(screen.getByTestId("lane-done")).toBeInTheDocument();
  });

  it("renders a household-shared email whose source_id is NOT one of the viewer's mailboxes", async () => {
    // The viewer owns only src-A, but the household-union query returns Bob's
    // shared email (src-B). It must render — proves we do NOT filter to own sources.
    mocks.listSources.mockResolvedValue({ sources: [imapSource("src-A")], total: 1 });
    mocks.listSignals.mockResolvedValue({
      signals: [sig({ signal_id: "bob", source_id: "src-B", scope: "shared", title: "Bob shared this", from_display: "Bob" })],
      total: 1,
    });
    renderView();
    await waitFor(() => expect(screen.getByText("Bob shared this")).toBeInTheDocument());
    expect(screen.getByText("Bob")).toBeInTheDocument();
  });

  it("collapses an owner's personal original + shared projection into one card (LUM-661)", async () => {
    mocks.listSources.mockResolvedValue({ sources: [imapSource("src-A")], total: 1 });
    mocks.listSignals.mockResolvedValue({
      signals: [
        sig({ signal_id: "orig", scope: "personal", title: "Lease renewal" }),
        sig({ signal_id: "proj", scope: "shared", published_from: "orig", title: "Lease renewal" }),
      ],
      total: 2,
    });
    renderView();
    await waitFor(() => expect(screen.getAllByText("Lease renewal")).toHaveLength(1));
  });

  it("shows the connect-a-mailbox state when no IMAP source is configured", async () => {
    mocks.listSources.mockResolvedValue({ sources: [], total: 0 });
    mocks.listSignals.mockResolvedValue({ signals: [], total: 0 });
    renderView();
    await waitFor(() => expect(screen.getByText(/Connect a mailbox in Settings/)).toBeInTheDocument());
  });

  it("shows an error state (distinct from loading) with a retry", async () => {
    mocks.listSources.mockRejectedValue(new Error("boom"));
    mocks.listSignals.mockResolvedValue({ signals: [], total: 0 });
    renderView();
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("Couldn't load email"));
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
  });

  it("filters by category and free text, and offers server-side search", async () => {
    mocks.listSources.mockResolvedValue({ sources: [imapSource("src-A")], total: 1 });
    mocks.listSignals.mockResolvedValue({
      signals: [
        sig({ signal_id: "sch", title: "School pickup", category: "school", category_label: "School", importance_score: 0.9 }),
        sig({ signal_id: "bill", title: "Electric bill", category: "finance", category_label: "Finance", importance_score: 0.9 }),
      ],
      total: 2,
    });
    renderView();
    await waitFor(() => expect(screen.getByText("School pickup")).toBeInTheDocument());

    // link out to Unified Search (LUM-173)
    expect(screen.getByRole("link", { name: /Search all email/ })).toHaveAttribute("href", "/search");

    // category filter
    await userEvent.click(screen.getByRole("button", { name: "School" }));
    expect(screen.getByText("School pickup")).toBeInTheDocument();
    expect(screen.queryByText("Electric bill")).not.toBeInTheDocument();

    // reset + text filter
    await userEvent.click(screen.getByRole("button", { name: "All" }));
    await userEvent.type(screen.getByLabelText("Filter loaded email"), "electric");
    expect(screen.getByText("Electric bill")).toBeInTheDocument();
    expect(screen.queryByText("School pickup")).not.toBeInTheDocument();
  });

  // LUM-649: 👍/👎 feedback → in-the-moment toast naming the moved term.
  it("shows a 'flag fewer' toast when 'Not important' feedback demotes a term", async () => {
    mocks.listSources.mockResolvedValue({ sources: [imapSource("src-A")], total: 1 });
    mocks.listSignals.mockResolvedValue({
      signals: [sig({ signal_id: "n1", title: "Newsletter blast", importance_score: 0.1 })],
      total: 1,
    });
    mocks.setTriage.mockResolvedValue({});
    mocks.postSignalFeedback.mockResolvedValue({
      status: "ok", type: "explicit", positive: false,
      effect: { demoted: ["newsletter"], promoted: [] },
    });
    renderView();
    await waitFor(() => expect(screen.getByText("Newsletter blast")).toBeInTheDocument());

    await userEvent.click(screen.getByRole("button", { name: "Not important" }));

    await waitFor(() =>
      expect(screen.getByText(/I'll flag fewer like this: newsletter/)).toBeInTheDocument(),
    );
    expect(mocks.postSignalFeedback).toHaveBeenCalledWith(expect.anything(), {
      itemId: "n1", positive: false,
    });
  });

  it("shows a 'flag more' toast when 👍 promotes a term", async () => {
    mocks.listSources.mockResolvedValue({ sources: [imapSource("src-A")], total: 1 });
    mocks.listSignals.mockResolvedValue({
      signals: [sig({ signal_id: "w1", title: "Work update", importance_score: 0.1 })],
      total: 1,
    });
    mocks.postSignalFeedback.mockResolvedValue({
      status: "ok", type: "explicit", positive: true,
      effect: { demoted: [], promoted: ["work"] },
    });
    renderView();
    await waitFor(() => expect(screen.getByText("Work update")).toBeInTheDocument());

    await userEvent.click(screen.getByRole("button", { name: "Flag more like this" }));

    await waitFor(() =>
      expect(screen.getByText(/I'll flag more like this: work/)).toBeInTheDocument(),
    );
  });

  // LUM-672: the informational toast fades on its own instead of lingering.
  // Real timers (fake timers deadlock react-query's mocked-promise loading); the
  // component's own 6s setTimeout drives the removal, so allow >6s for this case.
  it("auto-dismisses the feedback toast after ~6s", async () => {
    mocks.listSources.mockResolvedValue({ sources: [imapSource("src-A")], total: 1 });
    mocks.listSignals.mockResolvedValue({
      signals: [sig({ signal_id: "w1", title: "Work update", importance_score: 0.1 })],
      total: 1,
    });
    mocks.postSignalFeedback.mockResolvedValue({
      status: "ok", type: "explicit", positive: true,
      effect: { demoted: [], promoted: ["work"] },
    });
    renderView();
    await waitFor(() => expect(screen.getByText("Work update")).toBeInTheDocument());

    await userEvent.click(screen.getByRole("button", { name: "Flag more like this" }));
    const toast = await screen.findByText(/I'll flag more like this: work/);

    await waitForElementToBeRemoved(toast, { timeout: 8000 });
  }, 10000);
});

// --------------------------------------------------------------------------- //
// LUM-821 — which mailbox a card arrived in
// --------------------------------------------------------------------------- //
//
// These lanes are cross-mailbox and always have been (`source_type: "imap"`, no
// `source_id` — asserted at the top of this file). That is the decided shape,
// unified read and per-mailbox act, but it leaves a multi-mailbox account
// reading personal and work mail interleaved with nothing saying which is
// which. The card renders whatever label it is handed; every decision about
// WHETHER to hand it one lives here, because it depends on the source list and
// not on any single row.
describe("EmailInboxView — mailbox indicator (LUM-821)", () => {
  it("names the mailbox when the viewer has more than one", async () => {
    mocks.listSources.mockResolvedValue({
      sources: [imapSource("src-A"), imapSource("src-B")],
      total: 2,
    });
    mocks.listSignals.mockResolvedValue({
      signals: [sig({ signal_id: "m1", source_id: "src-A", source_label: "Mailbox src-A" })],
      total: 1,
    });
    renderView();

    await waitFor(() => expect(screen.getByText("Title")).toBeInTheDocument());
    expect(screen.getByTitle("Mailbox: Mailbox src-A")).toBeInTheDocument();
  });

  it("says nothing when the viewer has only one mailbox", async () => {
    // The answer is never in doubt, so the label would be a word repeated on
    // every card that tells the user nothing.
    mocks.listSources.mockResolvedValue({ sources: [imapSource("src-A")], total: 1 });
    mocks.listSignals.mockResolvedValue({
      signals: [sig({ signal_id: "m1", source_id: "src-A", source_label: "Mailbox src-A" })],
      total: 1,
    });
    renderView();

    await waitFor(() => expect(screen.getByText("Title")).toBeInTheDocument());
    expect(screen.queryByTitle(/^Mailbox: /)).toBeNull();
  });

  it("does not name another household member's mailbox", async () => {
    // The indicator exists so a reply leaves from the right account, and this
    // card offers no reply at all (LUM-719). Naming Bob's mailbox would
    // disclose his account naming to answer a question the card does not pose.
    mocks.listSources.mockResolvedValue({
      sources: [imapSource("src-A"), imapSource("src-B")],
      total: 2,
    });
    mocks.listSignals.mockResolvedValue({
      signals: [
        sig({
          signal_id: "bob",
          source_id: "src-Z",
          scope: "shared",
          title: "Bob shared this",
          source_label: "Bob private",
        }),
      ],
      total: 1,
    });
    renderView();

    await waitFor(() => expect(screen.getByText("Bob shared this")).toBeInTheDocument());
    expect(screen.queryByText("Bob private")).toBeNull();
  });

  it("says nothing for a row that carries no label", async () => {
    // Rows written before `source_label` was denormalized carry NULL. Falling
    // back to silence is the pre-LUM-821 behaviour, not an empty chip.
    mocks.listSources.mockResolvedValue({
      sources: [imapSource("src-A"), imapSource("src-B")],
      total: 2,
    });
    mocks.listSignals.mockResolvedValue({
      signals: [sig({ signal_id: "old", source_id: "src-A", source_label: null })],
      total: 1,
    });
    renderView();

    await waitFor(() => expect(screen.getByText("Title")).toBeInTheDocument());
    expect(screen.queryByTitle(/^Mailbox: /)).toBeNull();
  });
});


// --------------------------------------------------------------------------- //
// LUM-830 — starting a message rather than answering one
// --------------------------------------------------------------------------- //
//
// A reply inherits its mailbox from the message it answers. A composed message
// has none, so the surface must offer only mailboxes that can ACTUALLY send —
// offering one with sending off reproduces LUM-719, an affordance that fails at
// the send.
describe("EmailInboxView — compose a new message (LUM-830)", () => {
  function smtpFor(...sourceIds: string[]) {
    return { items: sourceIds.map((id) => ({ connector: "smtp", instance: id })) };
  }

  it("offers New message when a mailbox can send", async () => {
    mocks.listSources.mockResolvedValue({ sources: [imapSource("src-A")], total: 1 });
    mocks.listSignals.mockResolvedValue({ signals: [], total: 0 });
    mocks.listMyCredentials.mockResolvedValue(smtpFor("src-A"));
    renderView();

    expect(await screen.findByRole("button", { name: "New message" })).toBeInTheDocument();
  });

  it("explains itself instead of hiding when no mailbox can send", async () => {
    // Sending is something the user can switch on, so a missing button is a
    // dead end — they have no way to learn why they cannot write an email.
    mocks.listSources.mockResolvedValue({ sources: [imapSource("src-A")], total: 1 });
    mocks.listSignals.mockResolvedValue({ signals: [], total: 0 });
    mocks.listMyCredentials.mockResolvedValue({ items: [] });
    renderView();

    expect(await screen.findByText(/Turn on sending for a mailbox/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "New message" })).toBeNull();
  });

  it("says nothing at all when the credentials call fails", async () => {
    // Claiming sending is off for a mailbox whose state could not be read is
    // the mistake `deriveSendState` exists to avoid — it invites the user to
    // switch on something that is already on.
    mocks.listSources.mockResolvedValue({ sources: [imapSource("src-A")], total: 1 });
    mocks.listSignals.mockResolvedValue({ signals: [], total: 0 });
    mocks.listMyCredentials.mockRejectedValue(new Error("boom"));
    renderView();

    await waitFor(() => expect(screen.getByTestId("email-inbox-view")).toBeInTheDocument());
    expect(screen.queryByText(/Turn on sending for a mailbox/)).toBeNull();
    expect(screen.queryByRole("button", { name: "New message" })).toBeNull();
  });

  it("only offers mailboxes that can send, not every mailbox", async () => {
    // src-B has no SMTP credential, so it must not appear in the From picker
    // even though the user owns it.
    mocks.listSources.mockResolvedValue({
      sources: [imapSource("src-A"), imapSource("src-B")],
      total: 2,
    });
    mocks.listSignals.mockResolvedValue({ signals: [], total: 0 });
    mocks.listMyCredentials.mockResolvedValue(smtpFor("src-A"));
    renderView();

    await userEvent.click(await screen.findByRole("button", { name: "New message" }));

    // One sendable mailbox → named, not offered as a choice.
    expect(await screen.findByText("Mailbox src-A")).toBeInTheDocument();
    expect(screen.queryByText("Mailbox src-B")).toBeNull();
  });


  it("stops triage shortcuts firing behind the open composer", async () => {
    // The dialog is modal to the eye, not to the keydown listener. Without
    // `composeOpen` in the guard, `?` opens the shortcuts overlay UNDERNEATH
    // the composer, and `e`/`s`/`u` triage whatever card the cursor was on
    // while the user believes they are writing an email.
    mocks.listSources.mockResolvedValue({ sources: [imapSource("src-A")], total: 1 });
    mocks.listSignals.mockResolvedValue({ signals: [sig({ signal_id: "s1" })], total: 1 });
    mocks.listMyCredentials.mockResolvedValue(smtpFor("src-A"));
    renderView();

    await userEvent.click(await screen.findByRole("button", { name: "New message" }));
    await userEvent.keyboard("?");

    expect(screen.queryByRole("dialog", { name: /shortcut/i })).toBeNull();
    expect(mocks.setTriage).not.toHaveBeenCalled();
  });

  it("opens a blank composer, not a reply", async () => {
    mocks.listSources.mockResolvedValue({ sources: [imapSource("src-A")], total: 1 });
    mocks.listSignals.mockResolvedValue({ signals: [], total: 0 });
    mocks.listMyCredentials.mockResolvedValue(smtpFor("src-A"));
    renderView();

    await userEvent.click(await screen.findByRole("button", { name: "New message" }));

    const to = (await screen.findByLabelText("To")) as HTMLInputElement;
    expect(to.value).toBe("");
    expect((screen.getByLabelText("Subject") as HTMLInputElement).value).toBe("");
  });
});
