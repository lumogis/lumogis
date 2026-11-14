// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-728 — the consent gate as the user meets it.
//
// There is no rule form in this product: creating a filing rule is a single
// click on "Always file from this sender", placed with the sender group because
// that is the moment the user knows the answer. So the gate is a two-click
// confirm rather than a dialog, and the load-bearing cases are about what must
// NOT happen on the first click.
//
// The one to read first is "a refusal files nothing". `onAlwaysFile` creates the
// rule AND accepts the pending attachments in one path; on a 422 neither may
// happen, or the product half-applies an action the user has not confirmed.

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "../../../src/api/client";
import {
  OVERLAP_CODE,
  paperlessOverlapWarning,
} from "../../../src/features/email/paperlessOverlapWarning";

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
  fetchAttachmentRules: vi.fn(),
  deleteAttachmentRule: vi.fn(),
  fetchPaperlessOverlap: vi.fn(),
  candidateKey: (c: { signal_id: string; part_index: number }) =>
    `${c.signal_id}:${c.part_index}`,
  explainAcceptSkip: () => "Not filed",
  explainUnreachable: (n: number) => `${n} unreachable`,
  STALE_RULE_EXPLANATION: "stale",
  DELETE_RULE_EXPLANATION: "delete",
}));
vi.mock("../../../src/api/emailAttachmentIngest", () => api);

import { AttachmentReviewList } from "../../../src/features/email/AttachmentReviewList";

const SOURCE = "11111111-1111-1111-1111-111111111111";
const client = {} as never;
const FROM = "billing@utility.example";
// LUM-742: group identity is (source, sender) — keying selectors on the sender
// alone is ambiguous the moment the same sender appears in two mailboxes.
const GROUP = `${SOURCE}:${FROM}`;


const MESSAGE = "Paperless is already watching this mailbox.";
const ADVISORY = "You also run paperless, and it watches a mailbox on this server.";

function refusal(): ApiError {
  return new ApiError(
    422,
    JSON.stringify({
      detail: {
        code: OVERLAP_CODE,
        message: MESSAGE,
        alternative: "a",
        recovery: "r",
      },
    }),
  );
}

function arm(overlap: Record<string, unknown> | null = null) {
  api.fetchCandidates.mockResolvedValue({
    groups: [
      {
        from_addr: FROM,
        from_display: "Utility Billing",
        source_id: SOURCE,
        source_name: "work@example.com",
        count: 1,
        candidates: [
          {
            signal_id: "sig-1",
            part_index: 0,
            filename: "invoice.pdf",
            mime_type: "application/pdf",
            size_bytes: 2048,
            from_addr: FROM,
            from_display: "Utility Billing",
            subject: "July invoice",
            category: "bills",
            received_at: "2026-07-01T09:00:00Z",
          },
        ],
      },
    ],
    total: 1,
    truncated: false,
    unreachable: 0,
  });
  api.fetchPaperlessOverlap.mockResolvedValue(
    overlap ?? { overlap: false, confidence: null, status: "ok", message: null },
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  api.acceptCandidates.mockResolvedValue({ filed: 1, skipped: [] });
});

// --- the recogniser ----------------------------------------------------------

describe("paperlessOverlapWarning", () => {
  it("matches on the code, not the copy", () => {
    expect(paperlessOverlapWarning(refusal())?.message).toBe(MESSAGE);
  });

  it("returns null for a different 422 on the same route", () => {
    // `invalid_rule` uses `error`, not `code`. Both are 422 on this route, and a
    // recogniser that matched loosely would swallow a real validation failure.
    const other = new ApiError(422, JSON.stringify({ detail: { error: "invalid_rule" } }));
    expect(paperlessOverlapWarning(other)).toBeNull();
  });

  it("returns null for non-422, non-ApiError, and unparseable bodies", () => {
    expect(paperlessOverlapWarning(new ApiError(500, "boom"))).toBeNull();
    expect(paperlessOverlapWarning(new Error("nope"))).toBeNull();
    expect(paperlessOverlapWarning(new ApiError(422, "not json"))).toBeNull();
  });
});

// --- the two-click confirm ---------------------------------------------------

describe("the overlap gate in the review list", () => {
  it("a refusal files NOTHING — not the rule, not the pending attachments", async () => {
    arm();
    api.createAttachmentRule.mockRejectedValue(refusal());
    render(<AttachmentReviewList client={client} sourceId={SOURCE} />);
    await screen.findByTestId(`review-always-${GROUP}`);

    await userEvent.click(screen.getByTestId(`review-always-${GROUP}`));

    await screen.findByTestId(`review-overlap-${GROUP}`);
    expect(api.acceptCandidates).not.toHaveBeenCalled();
  });

  it("does not show the generic retry copy — nothing failed, a question was asked", async () => {
    arm();
    api.createAttachmentRule.mockRejectedValue(refusal());
    render(<AttachmentReviewList client={client} sourceId={SOURCE} />);
    await screen.findByTestId(`review-always-${GROUP}`);

    await userEvent.click(screen.getByTestId(`review-always-${GROUP}`));

    await screen.findByTestId(`review-overlap-${GROUP}`);
    expect(screen.queryByText(/Couldn't save that rule/)).toBeNull();
    expect(screen.getByText(MESSAGE)).toBeTruthy();
  });

  it("a real failure still shows the retry copy", async () => {
    arm();
    api.createAttachmentRule.mockRejectedValue(new ApiError(500, "boom"));
    render(<AttachmentReviewList client={client} sourceId={SOURCE} />);
    await screen.findByTestId(`review-always-${GROUP}`);

    await userEvent.click(screen.getByTestId(`review-always-${GROUP}`));

    await screen.findByText(/Couldn't save that rule/);
  });

  it("the second click acknowledges, and both the rule and the pending files land", async () => {
    arm();
    api.createAttachmentRule.mockRejectedValueOnce(refusal()).mockResolvedValue({ id: "r1" });
    render(<AttachmentReviewList client={client} sourceId={SOURCE} />);
    await screen.findByTestId(`review-always-${GROUP}`);

    await userEvent.click(screen.getByTestId(`review-always-${GROUP}`));
    await userEvent.click(await screen.findByTestId(`review-overlap-confirm-${GROUP}`));

    await waitFor(() => expect(api.createAttachmentRule).toHaveBeenCalledTimes(2));
    expect(api.createAttachmentRule.mock.calls[1][5]).toBe(true);
    await waitFor(() => expect(api.acceptCandidates).toHaveBeenCalled());
  });
});

// --- the advisory ------------------------------------------------------------

describe("the low-confidence advisory", () => {
  it("renders once for the list and never gates", async () => {
    arm({ overlap: true, confidence: "low", status: "ok", message: ADVISORY });
    api.createAttachmentRule.mockResolvedValue({ id: "r1" });
    render(<AttachmentReviewList client={client} sourceId={SOURCE} />);

    await screen.findByTestId(`review-overlap-advisory-${SOURCE}`);
    expect(screen.getAllByTestId(`review-overlap-advisory-${SOURCE}`)).toHaveLength(1);

    // ...and the button is still the plain one — `low` must never gate.
    await userEvent.click(screen.getByTestId(`review-always-${GROUP}`));
    await waitFor(() => expect(api.acceptCandidates).toHaveBeenCalled());
  });

  it("is absent when there is no overlap", async () => {
    arm();
    render(<AttachmentReviewList client={client} sourceId={SOURCE} />);
    await screen.findByTestId(`review-always-${GROUP}`);
    expect(screen.queryByTestId(`review-overlap-advisory-${SOURCE}`)).toBeNull();
  });

  it("a failed probe disturbs nothing — the list still works", async () => {
    arm();
    api.fetchPaperlessOverlap.mockRejectedValue(new ApiError(503, "down"));
    render(<AttachmentReviewList client={client} sourceId={SOURCE} />);

    await screen.findByTestId(`review-always-${GROUP}`);
    expect(screen.queryByTestId(`review-overlap-advisory-${SOURCE}`)).toBeNull();
  });

  it("probes once per list, not once per sender group", async () => {
    arm();
    render(<AttachmentReviewList client={client} sourceId={SOURCE} />);
    await screen.findByTestId(`review-always-${GROUP}`);
    expect(api.fetchPaperlessOverlap).toHaveBeenCalledTimes(1);
  });
});

// --- review-round fixes ------------------------------------------------------

describe("defects found in review", () => {
  it("still renders the ask when the server omits `message`", async () => {
    // The recogniser falls back to "" for a missing message. A truthiness check
    // on that re-rendered the plain button, so the next click 422'd again: the
    // rule became uncreatable with no feedback at all.
    arm();
    api.createAttachmentRule.mockRejectedValue(
      new ApiError(422, JSON.stringify({ detail: { code: OVERLAP_CODE } })),
    );
    render(<AttachmentReviewList client={client} sourceId={SOURCE} />);
    await screen.findByTestId(`review-always-${GROUP}`);

    await userEvent.click(screen.getByTestId(`review-always-${GROUP}`));

    await screen.findByTestId(`review-overlap-confirm-${GROUP}`);
  });

  it("shows the alternative and the recovery, not just the consequence", async () => {
    // All three were parsed and only the first was rendered, which turns a
    // considered explanation into a bare error.
    arm();
    api.createAttachmentRule.mockRejectedValue(refusal());
    render(<AttachmentReviewList client={client} sourceId={SOURCE} />);
    await screen.findByTestId(`review-always-${GROUP}`);

    await userEvent.click(screen.getByTestId(`review-always-${GROUP}`));

    await screen.findByTestId(`review-overlap-alt-${GROUP}`);
    expect(screen.getByTestId(`review-overlap-recovery-${GROUP}`)).toBeTruthy();
  });

  it("does not carry one mailbox's consent over to another", async () => {
    // `ackedRef` survived a `sourceId` change, so answering for mailbox A
    // skipped the server gate for mailbox B entirely.
    arm();
    api.createAttachmentRule.mockRejectedValueOnce(refusal()).mockResolvedValue({ id: "r1" });
    const { rerender } = render(<AttachmentReviewList client={client} sourceId={SOURCE} />);
    await screen.findByTestId(`review-always-${GROUP}`);
    await userEvent.click(screen.getByTestId(`review-always-${GROUP}`));
    await userEvent.click(await screen.findByTestId(`review-overlap-confirm-${GROUP}`));
    await waitFor(() => expect(api.createAttachmentRule).toHaveBeenCalledTimes(2));

    rerender(<AttachmentReviewList client={client} sourceId={"33333333-3333-3333-3333-333333333333"} />);
    await screen.findByTestId(`review-always-${GROUP}`);
    await userEvent.click(screen.getByTestId(`review-always-${GROUP}`));

    await waitFor(() => expect(api.createAttachmentRule).toHaveBeenCalledTimes(3));
    expect(api.createAttachmentRule.mock.calls[2][5]).toBe(false);
  });

  it("drops a previous mailbox's advisory when the source changes", async () => {
    arm({ overlap: true, confidence: "low", status: "ok", message: ADVISORY });
    const { rerender } = render(<AttachmentReviewList client={client} sourceId={SOURCE} />);
    await screen.findByTestId(`review-overlap-advisory-${SOURCE}`);

    api.fetchPaperlessOverlap.mockResolvedValue({
      overlap: false,
      confidence: null,
      status: "ok",
      message: null,
    });
    rerender(<AttachmentReviewList client={client} sourceId={"33333333-3333-3333-3333-333333333333"} />);

    await waitFor(() => expect(screen.queryByTestId(`review-overlap-advisory-${SOURCE}`)).toBeNull());
  });
});
