// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-742 — `/email/attachments`: the activation branch, the count, and the
// mailbox label.
//
// The branch is the point. `AttachmentIngestToggle` defaults off and never
// enables itself, so routing the review list without an activation surface
// would have shown every existing user an empty page with nothing to do about
// it — fixing the invisibility and delivering nothing.

import { type ReactNode } from "react";
import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";

const mocks = vi.hoisted(() => ({
  listSources: vi.fn(),
  fetchCandidates: vi.fn(),
  fetchFiledAttachments: vi.fn(),
  fetchPaperlessOverlap: vi.fn(),
  setAttachmentIngest: vi.fn(),
}));

vi.mock("../../../src/api/emailSources", () => ({ listSources: mocks.listSources }));
vi.mock("../../../src/auth/AuthProvider", () => ({ useAuth: () => ({ client: {} }) }));
vi.mock("../../../src/api/emailAttachmentIngest", async (importOriginal) => {
  // PARTIAL mock: `groupKey` and the explain* helpers are pure and the
  // components depend on their real behaviour. Replacing the module wholesale
  // is what broke every render when `groupKey` was added.
  const actual = await importOriginal<Record<string, unknown>>();
  return {
    ...actual,
    fetchCandidates: mocks.fetchCandidates,
    fetchFiledAttachments: mocks.fetchFiledAttachments,
    fetchPaperlessOverlap: mocks.fetchPaperlessOverlap,
    setAttachmentIngest: mocks.setAttachmentIngest,
  };
});

import { EmailAttachmentsPage } from "../../../src/features/email/EmailAttachmentsPage";

const A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa";
const B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb";

function mailbox(id: string, over: Record<string, unknown> = {}) {
  return {
    id,
    name: id === A ? "work@example.com" : "home@example.com",
    source_type: "imap",
    url: `imap://h/${id}`,
    category: "email",
    active: true,
    poll_interval: 900,
    extraction_method: "imap_full_ingest",
    full_ingest_active: true,
    last_polled_at: null,
    last_signal_at: null,
    polling_active: true,
    attachment_ingest_enabled: false,
    attachment_ingest_since: null,
    ...over,
  };
}

function group(sourceId: string, over: Record<string, unknown> = {}) {
  return {
    from_addr: "billing@utility.example",
    from_display: "Utility Billing",
    source_id: sourceId,
    source_name: sourceId === A ? "work@example.com" : "home@example.com",
    count: 1,
    candidates: [
      {
        signal_id: `sig-${sourceId}`,
        part_index: 0,
        filename: "invoice.pdf",
        mime_type: "application/pdf",
        size_bytes: 1024,
        from_addr: "billing@utility.example",
        from_display: "Utility Billing",
        subject: "Invoice",
        category: "bills",
        received_at: "2026-07-01T09:00:00Z",
        source_id: sourceId,
        source_name: sourceId === A ? "work@example.com" : "home@example.com",
      },
    ],
    ...over,
  };
}

function candidates(groups: unknown[], over: Record<string, unknown> = {}) {
  return {
    groups,
    total: groups.length,
    pending_total: groups.length,
    truncated: false,
    unreachable: 0,
    truncated_sources: [],
    errored_sources: [],
    ...over,
  };
}

function wrapper({ children }: { children: ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return (
    <MemoryRouter initialEntries={["/email/attachments"]}>
      <QueryClientProvider client={qc}>{children}</QueryClientProvider>
    </MemoryRouter>
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  mocks.fetchFiledAttachments.mockResolvedValue({ rows: [], total: 0 });
  mocks.fetchPaperlessOverlap.mockResolvedValue({ overlap: false });
  mocks.fetchCandidates.mockResolvedValue(candidates([]));
});

describe("the activation branch", () => {
  it("shows the activation panel when no mailbox has filing enabled", async () => {
    mocks.listSources.mockResolvedValue({ sources: [mailbox(A)], total: 1 });

    render(<EmailAttachmentsPage />, { wrapper });

    // The empty state IS the on-switch — not a link to settings, and not a
    // bare "nothing here" that leaves the feature undiscoverable forever.
    expect(await screen.findByTestId("attachment-activation")).toBeTruthy();
    expect(await screen.findByTestId(`attachment-activation-${A}`)).toBeTruthy();
  });

  it("shows the review list once a mailbox is enabled", async () => {
    mocks.listSources.mockResolvedValue({
      sources: [mailbox(A, { attachment_ingest_enabled: true })],
      total: 1,
    });
    mocks.fetchCandidates.mockResolvedValue(candidates([group(A)]));

    render(<EmailAttachmentsPage />, { wrapper });

    await waitFor(() => expect(screen.getByTestId("review-groups")).toBeTruthy());
    expect(screen.queryByTestId("attachment-activation")).toBeNull();
  });

  it("explains a pending-operator-enable mailbox instead of offering a dead toggle", async () => {
    // extraction_method is right, so the toggle's own refusal does not fire —
    // but the operator flag is off, so enabling would never produce a candidate.
    // That is the silent no-op the panel exists to prevent.
    mocks.listSources.mockResolvedValue({
      sources: [mailbox(A, { full_ingest_active: false })],
      total: 1,
    });

    render(<EmailAttachmentsPage />, { wrapper });

    expect(await screen.findByTestId(`attachment-activation-blocked-${A}`)).toBeTruthy();
  });

  it("warns that re-enabling brings back a backlog, rather than promising only new mail", async () => {
    // A mailbox with an existing watermark keeps it on re-enable, so everything
    // accumulated while filing was off returns at once.
    mocks.listSources.mockResolvedValue({
      sources: [mailbox(A, { attachment_ingest_since: "2026-01-01T00:00:00Z" })],
      total: 1,
    });

    render(<EmailAttachmentsPage />, { wrapper });

    const outcome = await screen.findByTestId(`attachment-activation-outcome-${A}`);
    expect(outcome.textContent).toMatch(/before/i);
    expect(outcome.textContent).not.toMatch(/^Attachments that arrive from now on/);
  });
});

describe("the mailbox label", () => {
  it("is shown when the list spans more than one mailbox", async () => {
    mocks.listSources.mockResolvedValue({
      sources: [
        mailbox(A, { attachment_ingest_enabled: true }),
        mailbox(B, { attachment_ingest_enabled: true }),
      ],
      total: 2,
    });
    mocks.fetchCandidates.mockResolvedValue(candidates([group(A), group(B)]));

    render(<EmailAttachmentsPage />, { wrapper });

    expect(
      await screen.findByTestId(`review-group-source-${A}:billing@utility.example`),
    ).toBeTruthy();
  });

  it("is hidden for a single-mailbox household, where it would never vary", async () => {
    mocks.listSources.mockResolvedValue({
      sources: [mailbox(A, { attachment_ingest_enabled: true })],
      total: 1,
    });
    mocks.fetchCandidates.mockResolvedValue(candidates([group(A)]));

    render(<EmailAttachmentsPage />, { wrapper });

    await waitFor(() => expect(screen.getByTestId("review-groups")).toBeTruthy());
    expect(
      screen.queryByTestId(`review-group-source-${A}:billing@utility.example`),
    ).toBeNull();
  });
});

describe("what the page reports about its own limits", () => {
  it("names the mailbox that has more than this page could show", async () => {
    mocks.listSources.mockResolvedValue({
      sources: [mailbox(A, { attachment_ingest_enabled: true })],
      total: 1,
    });
    mocks.fetchCandidates.mockResolvedValue(
      candidates([group(A)], {
        truncated: true,
        truncated_sources: [{ source_id: A, source_name: "work@example.com" }],
      }),
    );

    render(<EmailAttachmentsPage />, { wrapper });

    const note = await screen.findByTestId("attachments-truncated-sources");
    expect(note.textContent).toContain("work@example.com");
  });

  it("says which mailbox it could not check, rather than omitting it silently", async () => {
    mocks.listSources.mockResolvedValue({
      sources: [mailbox(A, { attachment_ingest_enabled: true })],
      total: 1,
    });
    mocks.fetchCandidates.mockResolvedValue(
      candidates([group(A)], {
        errored_sources: [{ source_id: B, source_name: "home@example.com" }],
      }),
    );

    render(<EmailAttachmentsPage />, { wrapper });

    const note = await screen.findByTestId("attachments-errored-sources");
    expect(note.textContent).toContain("home@example.com");
  });
});

describe("the route with nothing to show", () => {
  it("redirects away when the household has no mailbox at all", async () => {
    mocks.listSources.mockResolvedValue({ sources: [], total: 0 });

    render(<EmailAttachmentsPage />, { wrapper });

    // Nothing to activate, so the panel must not render either.
    await waitFor(() =>
      expect(screen.queryByTestId("attachment-activation")).toBeNull(),
    );
  });
});
