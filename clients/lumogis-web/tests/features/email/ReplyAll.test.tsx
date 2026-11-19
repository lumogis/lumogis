// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-705 — Reply all on /email.
//
// The two cases that matter most: the pre-filled Cc must be VISIBLE (a filled
// field behind a collapsed disclosure is how this feature mails four people by
// surprise), and the disclosure's `open` must be mount-constant so a user who
// collapses it and then edits a recipient does not watch it spring back.

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
const sendMocks = vi.hoisted(() => ({
  fetchReplyAllRecipients: vi.fn(),
  fetchQuote: vi.fn(),
  fetchReplyDraft: vi.fn(),
  refineText: vi.fn(),
}));
vi.mock("../../../src/api/emailSources", () => mocks);
vi.mock("../../../src/api/emailSend", () => sendMocks);
vi.mock("../../../src/auth/AuthProvider", () => ({ useAuth: () => ({ client: {} }) }));

import { EmailInboxView } from "../../../src/features/email/EmailInboxView";

function wire(cc: string[], extra: Record<string, unknown> = {}) {
  mocks.listSources.mockResolvedValue({
    sources: [
      {
        id: "src-A", name: "Mailbox", source_type: "imap", url: "imap://h/1",
        category: "email", active: true, poll_interval: 900,
        extraction_method: "imap_metadata", full_ingest_active: false,
        last_polled_at: "2026-07-20T10:00:00Z", last_signal_at: null, polling_active: true,
      },
    ],
    total: 1,
  });
  const s = {
    signal_id: "s1", source_id: "src-A", title: "School trip", url: "imap://src-A/1/1",
    category: "", category_label: "", scope: "personal",
    published_at: "2026-07-20T09:00:00Z", content_summary: "",
    from_addr: "sender@example.com", from_display: "Sender", importance_score: 0.9,
  };
  mocks.listSignals.mockImplementation((_c: unknown, p?: { triaged?: string }) =>
    Promise.resolve(p?.triaged ? { signals: [], total: 0 } : { signals: [s], total: 1 }),
  );
  sendMocks.fetchReplyAllRecipients.mockResolvedValue({
    to: ["sender@example.com"],
    cc,
    degraded: false,
    dropped_self: true,
    truncated: 0,
    large_thread: false,
    ...extra,
  });
  // Both return a STRING, not a wrapper — the composer calls .trim() on them.
  sendMocks.fetchQuote.mockResolvedValue("");
  sendMocks.fetchReplyDraft.mockResolvedValue("");
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

async function openReplyAll(user: ReturnType<typeof userEvent.setup>) {
  await screen.findByTestId("email-card-s1");
  await user.click(within(screen.getByTestId("email-card-s1")).getByText("Reply all"));
}

afterEach(() => vi.clearAllMocks());

describe("Reply all", () => {
  it("pre-fills Cc and EXPANDS the disclosure so the recipients are visible", async () => {
    wire(["teacher@school.edu", "head@school.edu"]);
    const user = userEvent.setup();
    renderView();

    await openReplyAll(user);

    const disclosure = await screen.findByTestId("cc-bcc-disclosure");
    // The load-bearing assertion: not merely filled, but OPEN.
    expect(disclosure).toHaveAttribute("open");
    expect(screen.getByLabelText("Cc")).toHaveValue("teacher@school.edu, head@school.edu");
  });

  it("a manually collapsed disclosure stays collapsed while editing", async () => {
    // The regression a live-bound `open` would cause: collapse it, fix a typo,
    // and watch it spring back on every keystroke.
    wire(["teacher@school.edu"]);
    const user = userEvent.setup();
    renderView();
    await openReplyAll(user);

    const disclosure = await screen.findByTestId("cc-bcc-disclosure");
    await user.click(within(disclosure).getByText("Cc / Bcc"));
    expect(disclosure).not.toHaveAttribute("open");

    await user.type(screen.getByLabelText("Cc"), ", extra@x.com");

    expect(disclosure).not.toHaveAttribute("open");
  });

  it("a degraded response opens a plain Reply and says so", async () => {
    wire([], { degraded: true });
    const user = userEvent.setup();
    renderView();

    await openReplyAll(user);

    expect(await screen.findByText(/replying to the sender only/i)).toBeInTheDocument();
    expect(screen.getByLabelText("Cc")).toHaveValue("");
  });

  it("a lookup failure still opens a usable composer", async () => {
    wire([]);
    sendMocks.fetchReplyAllRecipients.mockRejectedValueOnce(new Error("network"));
    const user = userEvent.setup();
    renderView();

    await openReplyAll(user);

    expect(await screen.findByText(/replying to the sender only/i)).toBeInTheDocument();
    expect(screen.getByLabelText("Cc")).toBeInTheDocument();
  });

  it("a 404 says the mailbox is not yours, not that the fetch failed", async () => {
    // The signal is visible because a household member SHARED it, but it lives in
    // their mailbox — so reply-all can never work for it. Reporting that with the
    // transient "couldn't read the other recipients" wording sends the user off to
    // check their connection over something permanent.
    wire([]);
    const { ApiError } = await import("../../../src/api/client");
    sendMocks.fetchReplyAllRecipients.mockRejectedValueOnce(new ApiError(404, "not_found"));
    const user = userEvent.setup();
    renderView();

    await openReplyAll(user);

    const notice = await screen.findByText(/another household member's mailbox/i);
    expect(notice.closest("[role=alert]")).not.toBeNull();
    expect(screen.queryByText(/replying to the sender only/i)).toBeNull();
  });

  it("shows the truncation count BEFORE send, not after", async () => {
    wire(["a@x.com"], { truncated: 7 });
    const user = userEvent.setup();
    renderView();

    await openReplyAll(user);

    expect(await screen.findByText(/7 further recipients were left out/i)).toBeInTheDocument();
  });

  it("warns prominently on a large thread", async () => {
    const many = Array.from({ length: 30 }, (_, i) => `u${i}@x.com`);
    wire(many, { large_thread: true });
    const user = userEvent.setup();
    renderView();

    await openReplyAll(user);

    const warning = await screen.findByText(/goes to 30 people/i);
    expect(warning.closest("[role=alert]")).not.toBeNull();
  });

  it("a pre-filled Cc survives the composer's own split back into addresses", async () => {
    // The seam the backend and web each got individually right and jointly wrong:
    // the service used to return `Name <addr>` display forms, the composer joins
    // them with ", " and re-splits on ",", and the send path validates each token
    // against a regex that permits neither spaces nor angle brackets. Every unit
    // test on both sides passed and a real send 422'd.
    wire(["jane@x.com", "alice@x.com"]);
    const user = userEvent.setup();
    renderView();
    await openReplyAll(user);

    const value = (await screen.findByLabelText("Cc")).getAttribute("value") ?? "";
    const tokens = value.split(",").map((s) => s.trim()).filter(Boolean);
    expect(tokens).toEqual(["jane@x.com", "alice@x.com"]);
    // Same shape the server's _ADDR_RE enforces: no spaces, no angle brackets.
    for (const tok of tokens) expect(tok).toMatch(/^[^@\s]+@[^@\s]+\.[^@\s]+$/);
  });

  it("the user can delete a pre-filled recipient — it is a proposal, not a fact", async () => {
    wire(["teacher@school.edu"]);
    const user = userEvent.setup();
    renderView();
    await openReplyAll(user);

    const field = await screen.findByLabelText("Cc");
    await user.clear(field);
    await user.type(field, "someone-else@x.com");

    expect(field).toHaveValue("someone-else@x.com");
  });

  it("Reply all is absent when the signal has no sender to reply to", async () => {
    wire([]);
    mocks.listSignals.mockImplementation((_c: unknown, p?: { triaged?: string }) =>
      Promise.resolve(
        p?.triaged
          ? { signals: [], total: 0 }
          : {
              signals: [
                {
                  signal_id: "s1", source_id: "src-A", title: "No sender",
                  url: "imap://src-A/1/1", category: "", category_label: "",
                  scope: "personal", published_at: "2026-07-20T09:00:00Z",
                  content_summary: "", from_addr: "", from_display: "",
                  importance_score: 0.9,
                },
              ],
              total: 1,
            },
      ),
    );
    renderView();

    await screen.findByTestId("email-card-s1");
    expect(
      within(screen.getByTestId("email-card-s1")).queryByText("Reply all"),
    ).toBeNull();
  });

  it("hides every send action on a signal from another member's mailbox", async () => {
    // The /email inbox is a household UNION, so a signal another member shared is
    // visible here while living in THEIR account. Sending needs that mailbox's
    // SMTP credentials, which this user has none of — Reply all 404s and
    // Reply/Forward fail later, at send. Offering an action that cannot work is
    // worse than not offering it. (LUM-719)
    wire([]);
    mocks.listSignals.mockImplementation((_c: unknown, p?: { triaged?: string }) =>
      Promise.resolve(
        p?.triaged
          ? { signals: [], total: 0 }
          : {
              signals: [
                {
                  signal_id: "s1",
                  // NOT "src-A" — this belongs to another member's mailbox.
                  source_id: "src-someone-else",
                  title: "Shared with me", url: "imap://src-someone-else/1/1",
                  category: "", category_label: "", scope: "shared",
                  published_at: "2026-07-20T09:00:00Z", content_summary: "",
                  from_addr: "sender@example.com", from_display: "Sender",
                  importance_score: 0.9,
                },
              ],
              total: 1,
            },
      ),
    );
    renderView();

    const card = await screen.findByTestId("email-card-s1");
    expect(within(card).queryByText("Reply all")).toBeNull();
    expect(within(card).queryByText("Reply")).toBeNull();
    expect(within(card).queryByText("Forward")).toBeNull();
    // Triage still works — the signal is legitimately visible and triageable.
    expect(within(card).getByText("Done")).toBeInTheDocument();
  });

  it("a plain Reply does not request reply-all recipients", async () => {
    wire(["teacher@school.edu"]);
    const user = userEvent.setup();
    renderView();
    await screen.findByTestId("email-card-s1");

    await user.click(within(screen.getByTestId("email-card-s1")).getByText("Reply"));

    await waitFor(() => expect(screen.getByLabelText("Cc")).toBeInTheDocument());
    expect(sendMocks.fetchReplyAllRecipients).not.toHaveBeenCalled();
    expect(screen.getByTestId("cc-bcc-disclosure")).not.toHaveAttribute("open");
  });
});
