// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-727 — the review list and the filing rules.
//
// The behaviours here are the ones that make the surface trustworthy rather than
// merely present:
//
//   * accept is NOT optimistic and decline IS — accept spends a real
//     fetch+extract+embed per file and can fail per-file, so removing rows before
//     the server says which landed would claim filings that did not happen;
//     decline moves no bytes, so only the write can fail,
//   * "Always file from this sender" both creates the rule AND files what is
//     already waiting — a user who says "always" and then still sees four
//     pending invoices concludes the rule did not work,
//   * truncation is stated, never silent, and
//   * deleting a rule does not unfile anything, and says so.

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  fetchCandidates: vi.fn(),
  acceptCandidates: vi.fn(),
  declineCandidates: vi.fn(),
  undoDecline: vi.fn(),
  createAttachmentRule: vi.fn(),
  // LUM-742: the composite (source, sender) identity. Mirrors the real export —
  // this mock replaces the module wholesale, so a new export must be added here
  // or every render throws on the missing symbol.
  groupKey: (g: { from_addr: string; source_id?: string }) =>
    `${g.source_id ?? ""}:${g.from_addr}`,
  // LUM-728 — the list probes for a paperless overlap on load. Resolved to
  // "no overlap" here so these cases keep testing what they were written for.
  fetchPaperlessOverlap: vi.fn(),
  fetchPaperlessOverlapAll: vi.fn(),
  fetchAttachmentRules: vi.fn(),
  deleteAttachmentRule: vi.fn(),
  candidateKey: (c: { signal_id: string; part_index: number }) =>
    `${c.signal_id}:${c.part_index}`,
  explainAcceptSkip: (r: string) =>
    ({
      not_found: "No longer available",
      over_limit: "Not reached this time — select fewer and try again",
      unavailable: "Couldn't read it — the original email may have been deleted",
      duplicate: "Already in your library",
    })[r] ?? "Not filed",
  explainUnreachable: (n: number) =>
    `${n} attachment${n === 1 ? "" : "s"} can no longer be opened — the original ` +
    `message${n === 1 ? " was" : "s were"} moved or deleted in your mailbox.`,
  STALE_RULE_EXPLANATION:
    "This category no longer exists, so the rule can't file anything. Delete it " +
    "or recreate the category.",
  DELETE_RULE_EXPLANATION:
    "Stops filing new attachments. Anything already filed stays in your library.",
}));
vi.mock("../../../src/api/emailAttachmentIngest", () => api);

import { AttachmentReviewList } from "../../../src/features/email/AttachmentReviewList";
import { AttachmentRuleList } from "../../../src/features/email/AttachmentRuleList";

// LUM-836: the rules list now reads the paperless overlap through React Query,
// so it needs a provider. Its host (`EmailSignalsView`) has always had one; only
// this test rendered it bare. `retry: false` mirrors the app-wide AuthProvider
// default so a failed query settles instead of retrying through the test.
function renderRules(sourceId = SOURCE) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <AttachmentRuleList client={client} sourceId={sourceId} />
    </QueryClientProvider>,
  );
}

const SOURCE = "11111111-1111-1111-1111-111111111111";
const client = {} as never;

function candidate(over: Partial<Record<string, unknown>> = {}) {
  return {
    signal_id: "sig-1",
    part_index: 0,
    filename: "invoice.pdf",
    mime_type: "application/pdf",
    size_bytes: 2048,
    from_addr: "billing@utility.example",
    from_display: "Utility Billing",
    subject: "Your July invoice",
    category: "bills",
    received_at: "2026-07-01T09:00:00Z",
    ...over,
  };
}

function group(over: Partial<Record<string, unknown>> = {}) {
  const candidates = (over.candidates as unknown[]) ?? [candidate()];
  return {
    from_addr: "billing@utility.example",
    from_display: "Utility Billing",
    // LUM-742: a group belongs to a mailbox now — the rule it offers is
    // per-source, so the button gates on this rather than on a page-level prop.
    source_id: SOURCE,
    source_name: "work@example.com",
    count: candidates.length,
    ...over,
    candidates,
  };
}

function respond(groups: unknown[], truncated = false, unreachable = 0) {
  api.fetchPaperlessOverlap.mockResolvedValue({
    overlap: false,
    confidence: null,
    status: "ok",
    message: null,
  });
  api.fetchCandidates.mockResolvedValue({
    groups,
    total: groups.reduce(
      (n: number, g) => n + (g as { candidates: unknown[] }).candidates.length,
      0,
    ),
    truncated,
    unreachable,
  });
}

describe("the review list", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.acceptCandidates.mockResolvedValue({ accepted: 1, skipped: [] });
    api.declineCandidates.mockResolvedValue({ declined: 1 });
    api.createAttachmentRule.mockResolvedValue({ id: "r1" });
  });

  it("says nothing is waiting rather than rendering an empty box", async () => {
    respond([]);

    render(<AttachmentReviewList client={client} />);

    expect(await screen.findByTestId("review-empty")).toBeTruthy();
  });

  it("groups attachments by sender", async () => {
    respond([
      group(),
      group({
        from_addr: "hr@work.example",
        from_display: "HR",
        candidates: [candidate({ signal_id: "sig-9", filename: "contract.pdf" })],
      }),
    ]);

    render(<AttachmentReviewList client={client} />);

    await waitFor(() => expect(screen.getByTestId("review-groups")).toBeTruthy());
    expect(screen.getByTestId(`review-group-${SOURCE}:billing@utility.example`)).toBeTruthy();
    expect(screen.getByTestId(`review-group-${SOURCE}:hr@work.example`)).toBeTruthy();
  });

  it("shows the subject, because a file called scan_0043.pdf is not decidable alone", async () => {
    respond([group({ candidates: [candidate({ filename: "scan_0043.pdf" })] })]);

    render(<AttachmentReviewList client={client} />);

    expect(await screen.findByTestId("review-subject")).toBeTruthy();
    expect(screen.getByTestId("review-subject").textContent).toContain("July invoice");
  });

  it("states when the list was truncated instead of cutting it silently", async () => {
    respond([group()], true);

    render(<AttachmentReviewList client={client} />);

    expect(await screen.findByTestId("review-truncated")).toBeTruthy();
  });

  it("does not claim truncation when there is none", async () => {
    respond([group()]);

    render(<AttachmentReviewList client={client} />);

    await waitFor(() => expect(screen.getByTestId("review-groups")).toBeTruthy());
    expect(screen.queryByTestId("review-truncated")).toBeNull();
  });

  // --- accept ---------------------------------------------------------------

  it("files one attachment on its own File button", async () => {
    respond([group()]);
    const user = userEvent.setup();

    render(<AttachmentReviewList client={client} />);
    await user.click(await screen.findByRole("button", { name: /^file invoice\.pdf to library$/i }));

    await waitFor(() =>
      expect(api.acceptCandidates).toHaveBeenCalledWith(client, [
        { signal_id: "sig-1", part_index: 0 },
      ]),
    );
  });

  it("removes what it filed", async () => {
    respond([group()]);
    const user = userEvent.setup();

    render(<AttachmentReviewList client={client} />);
    await user.click(await screen.findByRole("button", { name: /^file invoice\.pdf to library$/i }));

    await waitFor(() => expect(screen.getByTestId("review-empty")).toBeTruthy());
  });

  it("KEEPS a row the server could not file, and says why", async () => {
    // The asymmetry that matters: accept is not optimistic, because a real
    // fetch+extract+embed can fail for one file and succeed for another.
    // Removing the row anyway would claim a filing that did not happen.
    respond([group()]);
    api.acceptCandidates.mockResolvedValue({
      accepted: 0,
      skipped: [{ signal_id: "sig-1", part_index: 0, reason: "unavailable" }],
    });
    const user = userEvent.setup();

    render(<AttachmentReviewList client={client} />);
    await user.click(await screen.findByRole("button", { name: /^file invoice\.pdf to library$/i }));

    await waitFor(() => expect(screen.getByTestId("review-status")).toBeTruthy());
    expect(screen.getByTestId("review-status").textContent).toContain("Couldn't read it");
    expect(screen.getByTestId("review-row-sig-1:0")).toBeTruthy();
  });

  it("names the reason rather than only counting what it skipped", async () => {
    respond([
      group({
        candidates: [candidate(), candidate({ signal_id: "sig-2", filename: "b.pdf" })],
      }),
    ]);
    api.acceptCandidates.mockResolvedValue({
      accepted: 1,
      skipped: [{ signal_id: "sig-2", part_index: 0, reason: "duplicate" }],
    });
    const user = userEvent.setup();

    render(<AttachmentReviewList client={client} />);
    await user.click(await screen.findByRole("checkbox", { name: /select all from/i }));
    await user.click(screen.getByTestId("review-accept-picked"));

    await waitFor(() =>
      expect(screen.getByTestId("review-status").textContent).toContain(
        "Already in your library",
      ),
    );
  });

  // --- decline --------------------------------------------------------------

  it("declines without spending anything", async () => {
    respond([group()]);
    const user = userEvent.setup();

    render(<AttachmentReviewList client={client} />);
    await user.click(await screen.findByRole("button", { name: /don't file invoice\.pdf/i }));

    await waitFor(() => expect(api.declineCandidates).toHaveBeenCalled());
    expect(api.acceptCandidates).not.toHaveBeenCalled();
  });

  it("puts the row back when a decline fails", async () => {
    respond([group()]);
    api.declineCandidates.mockRejectedValue(new Error("nope"));
    const user = userEvent.setup();

    render(<AttachmentReviewList client={client} />);
    await user.click(await screen.findByRole("button", { name: /don't file invoice\.pdf/i }));

    await waitFor(() => expect(screen.getByTestId("review-status")).toBeTruthy());
    expect(screen.getByTestId("review-row-sig-1:0")).toBeTruthy();
  });

  it("tells the user a decline can be undone", async () => {
    // Terminal in the database, reversible by the user. If the copy does not say
    // so, "Don't file" reads as irreversible and the user hesitates on every row.
    respond([group()]);
    const user = userEvent.setup();

    render(<AttachmentReviewList client={client} />);
    await user.click(await screen.findByRole("button", { name: /don't file invoice\.pdf/i }));

    await waitFor(() =>
      expect(screen.getByTestId("review-status").textContent).toMatch(/undo/i),
    );
  });

  // --- bulk -----------------------------------------------------------------

  it("selects a whole sender at once", async () => {
    respond([
      group({
        candidates: [candidate(), candidate({ signal_id: "sig-2", filename: "b.pdf" })],
      }),
    ]);
    const user = userEvent.setup();

    render(<AttachmentReviewList client={client} />);
    await user.click(await screen.findByRole("checkbox", { name: /select all from/i }));

    expect(screen.getByTestId("review-picked-count").textContent).toContain("2");
  });

  it("files the whole selection in one call, not one per file", async () => {
    respond([
      group({
        candidates: [candidate(), candidate({ signal_id: "sig-2", filename: "b.pdf" })],
      }),
    ]);
    api.acceptCandidates.mockResolvedValue({ accepted: 2, skipped: [] });
    const user = userEvent.setup();

    render(<AttachmentReviewList client={client} />);
    await user.click(await screen.findByRole("checkbox", { name: /select all from/i }));
    await user.click(screen.getByTestId("review-accept-picked"));

    await waitFor(() => expect(api.acceptCandidates).toHaveBeenCalledTimes(1));
    expect(api.acceptCandidates.mock.calls[0][1]).toHaveLength(2);
  });

  it("offers no bulk bar until something is selected", async () => {
    respond([group()]);

    render(<AttachmentReviewList client={client} />);

    await waitFor(() => expect(screen.getByTestId("review-groups")).toBeTruthy());
    expect(screen.queryByTestId("review-bulk-bar")).toBeNull();
  });

  // --- the rule, created from the flow --------------------------------------

  it("creates a sender rule from the group it is looking at", async () => {
    respond([group()]);
    const user = userEvent.setup();

    render(<AttachmentReviewList client={client} sourceId={SOURCE} />);
    await user.click(
      await screen.findByTestId(`review-always-${SOURCE}:billing@utility.example`),
    );

    await waitFor(() =>
      expect(api.createAttachmentRule).toHaveBeenCalledWith(
        client,
        SOURCE,
        "sender",
        "billing@utility.example",
        undefined,
        // LUM-728 — the first click never acknowledges. That is the whole gate:
        // an unacknowledged request is what gives the server the chance to ask.
        false,
      ),
    );
  });

  it("ALSO files what is already waiting from that sender", async () => {
    // The rule only governs future arrivals. A user who clicks "always" and then
    // still sees the four invoices in front of them concludes it did not work.
    respond([
      group({
        candidates: [candidate(), candidate({ signal_id: "sig-2", filename: "b.pdf" })],
      }),
    ]);
    api.acceptCandidates.mockResolvedValue({ accepted: 2, skipped: [] });
    const user = userEvent.setup();

    render(<AttachmentReviewList client={client} sourceId={SOURCE} />);
    await user.click(
      await screen.findByTestId(`review-always-${SOURCE}:billing@utility.example`),
    );

    await waitFor(() => expect(api.acceptCandidates).toHaveBeenCalled());
    expect(api.acceptCandidates.mock.calls[0][1]).toHaveLength(2);
  });

  it("offers a rule on the all-mailboxes view, because the GROUP carries the mailbox", async () => {
    // LUM-742 reversed this. It used to assert the opposite: without a
    // page-level `sourceId` the button was hidden, because a group keyed on
    // sender alone had no mailbox its rule could belong to. That made the
    // cross-mailbox list a treadmill — decide one attachment at a time forever,
    // with no exit and no paperless warning. The group now carries its source,
    // so the rule is unambiguous and the button belongs here.
    respond([group()]);

    render(<AttachmentReviewList client={client} />);

    await waitFor(() => expect(screen.getByTestId("review-groups")).toBeTruthy());
    expect(
      await screen.findByTestId(`review-always-${SOURCE}:billing@utility.example`),
    ).toBeTruthy();
  });

  it("still withholds the rule when a group has no mailbox at all", async () => {
    // Defensive: a group with no source has no rule anyone could mean. Not
    // reachable from the current server, which always sets it — but a button
    // that 422s on click is worse than one that is not offered.
    respond([group({ source_id: "", source_name: "" })]);

    render(<AttachmentReviewList client={client} />);

    await waitFor(() => expect(screen.getByTestId("review-groups")).toBeTruthy());
    expect(screen.queryByTestId(`review-always-:billing@utility.example`)).toBeNull();
  });

  it("does not carry one mailbox's paperless consent over to another", async () => {
    // THE invariant of the cross-mailbox surface. The server skips its overlap
    // probe whenever `acknowledge_paperless_overlap` is true, for ANY source,
    // so the client is the only thing binding an acknowledgement to a mailbox —
    // no backend test can cover this. Consent held as a single boolean (as it
    // was before LUM-742) silently defeats the LUM-728 gate on every other
    // mailbox. Do not delete or weaken this test.
    const OTHER = "22222222-2222-2222-2222-222222222222";
    respond([
      group(),
      group({ source_id: OTHER, source_name: "home@example.com", from_addr: "hr@work.example" }),
    ]);
    const { ApiError } = await import("../../../src/api/client");
    api.createAttachmentRule.mockRejectedValue(
      new ApiError(422, JSON.stringify({ detail: { code: "paperless_may_also_file" } })),
    );
    const user = userEvent.setup();

    render(<AttachmentReviewList client={client} />);

    // Acknowledge for the FIRST mailbox.
    await user.click(await screen.findByTestId(`review-always-${SOURCE}:billing@utility.example`));
    await user.click(
      await screen.findByTestId(`review-overlap-confirm-${SOURCE}:billing@utility.example`),
    );

    api.createAttachmentRule.mockClear();

    // Now act on the SECOND mailbox. Its rule must still be unacknowledged.
    await user.click(await screen.findByTestId(`review-always-${OTHER}:hr@work.example`));

    await waitFor(() => expect(api.createAttachmentRule).toHaveBeenCalled());
    const args = api.createAttachmentRule.mock.calls.at(-1)!;
    expect(args[1]).toBe(OTHER);
    expect(args[5]).toBe(false);
  });

  it("files nothing when the rule could not be saved", async () => {
    respond([group()]);
    api.createAttachmentRule.mockRejectedValue(new Error("409"));
    const user = userEvent.setup();

    render(<AttachmentReviewList client={client} sourceId={SOURCE} />);
    await user.click(
      await screen.findByTestId(`review-always-${SOURCE}:billing@utility.example`),
    );

    await waitFor(() => expect(screen.getByTestId("review-status")).toBeTruthy());
    expect(api.acceptCandidates).not.toHaveBeenCalled();
  });
});

describe("the filing rules list", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.deleteAttachmentRule.mockResolvedValue(undefined);
  });

  function rules(rs: unknown[]) {
    api.fetchAttachmentRules.mockResolvedValue({ rules: rs });
  }

  const rule = (over: Partial<Record<string, unknown>> = {}) => ({
    id: "r1",
    match_type: "sender",
    match_value: "billing@utility.example",
    created_at: "2026-07-01T00:00:00Z",
    stale: false,
    ...over,
  });

  it("points at the review list when there are no rules yet", async () => {
    rules([]);

    renderRules();

    expect((await screen.findByTestId("rules-empty")).textContent).toMatch(/reviewing/i);
  });

  it("describes a rule in terms of what it does", async () => {
    rules([rule()]);

    renderRules();

    expect((await screen.findByTestId("rule-description")).textContent).toContain(
      "billing@utility.example",
    );
  });

  it("marks a rule whose category was deleted, rather than hiding it", async () => {
    // It cannot fire. Listing it as if it worked is how a user concludes the
    // feature is broken; removing it silently would be us deciding what they
    // meant when they deleted a category.
    rules([rule({ id: "r2", match_type: "category", match_value: "gone", stale: true })]);

    renderRules();

    expect(await screen.findByTestId("rule-stale")).toBeTruthy();
  });

  it("does not mark a live rule stale", async () => {
    rules([rule()]);

    renderRules();

    await waitFor(() => expect(screen.getByTestId("rules-list")).toBeTruthy());
    expect(screen.queryByTestId("rule-stale")).toBeNull();
  });

  it("removes a rule", async () => {
    rules([rule()]);
    const user = userEvent.setup();

    renderRules();
    await user.click(await screen.findByRole("button", { name: /stop/i }));

    await waitFor(() => expect(api.deleteAttachmentRule).toHaveBeenCalledWith(client, "r1"));
  });

  it("says that stopping a rule leaves filed documents alone", async () => {
    // "Stop"/"Delete" reads like a retraction. It is not — `filed_by_rule_id` is
    // ON DELETE SET NULL, so those documents stay and keep their undo path.
    rules([rule()]);
    const user = userEvent.setup();

    renderRules();
    await user.click(await screen.findByRole("button", { name: /stop/i }));

    await waitFor(() =>
      expect(screen.getByTestId("rules-status").textContent).toMatch(
        /already filed stays in your library/i,
      ),
    );
  });

  it("puts the rule back when the delete fails", async () => {
    rules([rule()]);
    api.deleteAttachmentRule.mockRejectedValue(new Error("nope"));
    const user = userEvent.setup();

    renderRules();
    await user.click(await screen.findByRole("button", { name: /stop/i }));

    await waitFor(() => expect(screen.getByTestId("rules-status")).toBeTruthy());
    expect(screen.getByTestId("rule-r1")).toBeTruthy();
  });
});


// --- LUM-727 Phase 1c: terminal must not mean invisible ----------------------

describe("attachments the mailbox can no longer produce", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.acceptCandidates.mockResolvedValue({ accepted: 1, skipped: [] });
    api.declineCandidates.mockResolvedValue({ declined: 1 });
  });

  it("says so, rather than letting them vanish from the list", async () => {
    // `message_gone` is terminal by design — the bytes are unfetchable, so
    // re-offering is asking an unanswerable question. But the attachment used to
    // just stop appearing, and a user waiting to file an invoice would watch it
    // never arrive and conclude the feature was broken.
    respond([group()], false, 2);

    render(<AttachmentReviewList client={client} />);

    expect((await screen.findByTestId("review-unreachable")).textContent).toMatch(
      /can no longer be opened/i,
    );
  });

  it("says it in the EMPTY state too", async () => {
    // Where a user whose only pending attachments went unreachable actually
    // lands — and the one place an unexplained empty list is most misleading.
    respond([], false, 1);

    render(<AttachmentReviewList client={client} />);

    expect(await screen.findByTestId("review-empty")).toBeTruthy();
    expect(screen.getByTestId("review-unreachable")).toBeTruthy();
  });

  it("stays quiet when there are none", async () => {
    respond([group()], false, 0);

    render(<AttachmentReviewList client={client} />);

    await waitFor(() => expect(screen.getByTestId("review-groups")).toBeTruthy());
    expect(screen.queryByTestId("review-unreachable")).toBeNull();
  });

  it("reads naturally for exactly one", async () => {
    respond([], false, 1);

    render(<AttachmentReviewList client={client} />);

    expect((await screen.findByTestId("review-unreachable")).textContent).toContain(
      "1 attachment can",
    );
  });
});
