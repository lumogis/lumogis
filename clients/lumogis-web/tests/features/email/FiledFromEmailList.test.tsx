// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-702 — the "Filed from email" list and the per-mailbox opt-in.
//
// Three behaviours carry the weight here, and each of them is a promise the user
// can catch us breaking:
//
//   * a SKIP row explains itself and offers no Undo (nothing was filed, so the
//     button would promise an action that does nothing),
//   * an Undo that FAILS puts the row back (the server deletes vectors before
//     marking reversed, so a failure means nothing was removed), and
//   * a metadata-mode mailbox cannot reach the 422 — the control is disabled
//     with an explanation instead.

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  fetchFiledAttachments: vi.fn(),
  undoFiledAttachment: vi.fn(),
  undoAllForSource: vi.fn(),
  setAttachmentIngest: vi.fn(),
  explainSkip: (r: string | null) =>
    r
      ? ({
          no_extractor: "No text could be read from this file type",
          blocked_ext: "Not filed (executable file type)",
          too_large: "Too large to file automatically",
          no_text: "No text could be read",
          duplicate: "Already filed",
          message_gone: "The original message is no longer in the mailbox",
          ambiguous: "Couldn't identify the message this came from",
          declined: "You chose not to file this",
        } as Record<string, string>)[r]
      : null,
  isUndoable: (row: { skipped_reason: string | null; reversed_at: string | null }) =>
    row.skipped_reason === null && row.reversed_at === null,
  canFileAttachments: (m: string) => m === "imap_full_ingest",
  refileAttachment: vi.fn(),
  isRefilable: (row: { skipped_reason: string | null; reversed_at: string | null }) =>
    row.skipped_reason === null && row.reversed_at !== null,
  isUndecidable: (row: { skipped_reason: string | null }) => row.skipped_reason === "declined",
  undoDecline: vi.fn(),
  REFILE_EXPLANATIONS: {
    not_found: "That filing is no longer available to restore",
    duplicate: "Already filed — the same file arrived in another message",
    unavailable: "Couldn't read it again — the original email may have been deleted",
  },
  METADATA_MODE_EXPLANATION:
    "This mailbox only stores message headers, so its attachments were never " +
    "downloaded. Switch it to full ingest to review them for your library.",
}));
vi.mock("../../../src/api/emailAttachmentIngest", () => api);

import { FiledFromEmailList } from "../../../src/features/email/FiledFromEmailList";
import { AttachmentIngestToggle } from "../../../src/features/email/AttachmentIngestToggle";

function row(over: Record<string, unknown> = {}) {
  return {
    id: 1,
    signal_id: "email:s:1",
    part_index: 0,
    filename: "invoice.pdf",
    mime_type: "application/pdf",
    size_bytes: 2048,
    logical_path: "email-attachment://src/1/0",
    ingested_at: "2026-07-26T09:00:00Z",
    from_addr: "accounts@vendor.example",
    from_display: "Vendor Accounts",
    subject: "Your July invoice",
    reversed_at: null,
    skipped_reason: null,
    ...over,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
});

// --- 34 ---------------------------------------------------------------------

describe("the filed list", () => {
  it("renders a filing with its filename, size and time", async () => {
    api.fetchFiledAttachments.mockResolvedValue({ rows: [row()], total: 1 });

    render(<FiledFromEmailList client={{} as never} />);

    expect(await screen.findByTestId("filed-filename")).toHaveTextContent("invoice.pdf");
    expect(screen.getByTestId("filed-size")).toHaveTextContent("2 KB");
    expect(screen.getByRole("button", { name: /undo filing of invoice\.pdf/i })).toBeTruthy();
  });

  it("says which email it came from", async () => {
    // "invoice.pdf" arrives from a dozen senders — the filename alone does not
    // answer the question this surface exists for.
    api.fetchFiledAttachments.mockResolvedValue({ rows: [row()], total: 1 });

    render(<FiledFromEmailList client={{} as never} />);

    expect(await screen.findByTestId("filed-from")).toHaveTextContent("Vendor Accounts");
    expect(screen.getByTestId("filed-subject")).toHaveTextContent("Your July invoice");
  });

  it("still lists a filing whose signal has been purged", async () => {
    // The ledger deliberately keeps no FK on signal_id, so provenance can go
    // missing without the filing doing so.
    api.fetchFiledAttachments.mockResolvedValue({
      rows: [row({ from_addr: "", from_display: "", subject: "" })],
      total: 1,
    });

    render(<FiledFromEmailList client={{} as never} />);

    expect(await screen.findByTestId("filed-filename")).toHaveTextContent("invoice.pdf");
    expect(screen.queryByTestId("filed-from")).toBeNull();
  });

  it("explains a skipped row and offers no Undo", async () => {
    // A user who cannot find their scanned invoice needs to know the sweep
    // looked at it — an unexplained absence reads as a broken feature.
    api.fetchFiledAttachments.mockResolvedValue({
      rows: [row({ id: 2, filename: "photo.heic", skipped_reason: "no_extractor" })],
      total: 1,
    });

    render(<FiledFromEmailList client={{} as never} />);

    expect(await screen.findByTestId("filed-skip-reason")).toHaveTextContent(
      "No text could be read from this file type",
    );
    expect(screen.queryByRole("button", { name: /undo/i })).toBeNull();
  });

  it("offers no Undo on a row already reversed", async () => {
    api.fetchFiledAttachments.mockResolvedValue({
      rows: [row({ reversed_at: "2026-07-26T10:00:00Z" })],
      total: 1,
    });

    render(<FiledFromEmailList client={{} as never} />);

    expect(await screen.findByTestId("filed-reversed")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /undo/i })).toBeNull();
  });

  it("says so plainly when nothing has been filed", async () => {
    api.fetchFiledAttachments.mockResolvedValue({ rows: [], total: 0 });

    render(<FiledFromEmailList client={{} as never} />);

    expect(await screen.findByTestId("filed-empty")).toBeTruthy();
  });
});

// --- 35 ---------------------------------------------------------------------

describe("undo", () => {
  it("calls the endpoint once and removes the row optimistically", async () => {
    const user = userEvent.setup();
    api.fetchFiledAttachments.mockResolvedValue({ rows: [row()], total: 1 });
    api.undoFiledAttachment.mockResolvedValue({ reversed_count: 1 });

    render(<FiledFromEmailList client={{} as never} />);
    await user.click(await screen.findByRole("button", { name: /undo/i }));

    await waitFor(() => expect(screen.queryByTestId("filed-row-1")).toBeNull());
    expect(api.undoFiledAttachment).toHaveBeenCalledTimes(1);
    expect(api.undoFiledAttachment).toHaveBeenCalledWith(expect.anything(), 1);
  });

  it("restores the row when the undo fails", async () => {
    const user = userEvent.setup();
    api.fetchFiledAttachments.mockResolvedValue({ rows: [row()], total: 1 });
    api.undoFiledAttachment.mockRejectedValue(new Error("qdrant down"));

    render(<FiledFromEmailList client={{} as never} />);
    await user.click(await screen.findByRole("button", { name: /undo/i }));

    // The server deletes vectors BEFORE marking reversed, so a failure means
    // the document is still there. Showing it gone would be a lie.
    expect(await screen.findByTestId("filed-row-1")).toBeTruthy();
    expect(screen.getByTestId("filed-status")).toHaveTextContent(/try again/i);
  });

  it("restores the row when the server reports nothing was reversed", async () => {
    const user = userEvent.setup();
    api.fetchFiledAttachments.mockResolvedValue({ rows: [row()], total: 1 });
    api.undoFiledAttachment.mockResolvedValue({ reversed_count: 0 });

    render(<FiledFromEmailList client={{} as never} />);
    await user.click(await screen.findByRole("button", { name: /undo/i }));

    expect(await screen.findByTestId("filed-row-1")).toBeTruthy();
  });
});

// --- 36 ---------------------------------------------------------------------

describe("the mailbox opt-in", () => {
  it("is disabled with an explanation for a metadata-mode mailbox", async () => {
    render(
      <AttachmentIngestToggle
        client={{} as never}
        sourceId="src-1"
        extractionMethod="imap_metadata"
        enabled={false}
      />,
    );

    const box = screen.getByRole("checkbox", {
      name: /review this mailbox.s attachments/i,
    });
    expect(box).toBeDisabled();
    expect(screen.getByTestId("ingest-unsupported")).toHaveTextContent(
      api.METADATA_MODE_EXPLANATION,
    );
  });

  it("never lets a metadata-mode mailbox reach the 422", async () => {
    const user = userEvent.setup();
    render(
      <AttachmentIngestToggle
        client={{} as never}
        sourceId="src-1"
        extractionMethod="imap_metadata"
        enabled={false}
      />,
    );

    await user.click(
      screen.getByRole("checkbox", { name: /review this mailbox.s attachments/i }),
    );

    expect(api.setAttachmentIngest).not.toHaveBeenCalled();
  });

  it("defaults to off and enables go-forward only", async () => {
    const user = userEvent.setup();
    api.setAttachmentIngest.mockResolvedValue({ updated: true, apply_to_existing: false });

    render(
      <AttachmentIngestToggle
        client={{} as never}
        sourceId="src-1"
        extractionMethod="imap_full_ingest"
        enabled={false}
      />,
    );

    const box = screen.getByRole("checkbox", {
      name: /review this mailbox.s attachments/i,
    });
    expect(box).not.toBeChecked();

    await user.click(box);

    expect(api.setAttachmentIngest).toHaveBeenCalledWith(expect.anything(), "src-1", {
      enabled: true,
      apply_to_existing: false,
    });
  });

  it("passes apply_to_existing when the user asks for existing mail", async () => {
    const user = userEvent.setup();
    api.setAttachmentIngest.mockResolvedValue({ updated: true, apply_to_existing: true });

    render(
      <AttachmentIngestToggle
        client={{} as never}
        sourceId="src-1"
        extractionMethod="imap_full_ingest"
        enabled={false}
      />,
    );

    await user.click(
      screen.getByRole("checkbox", { name: /also include attachments from existing mail/i }),
    );
    await user.click(
      screen.getByRole("checkbox", { name: /review this mailbox.s attachments/i }),
    );

    expect(api.setAttachmentIngest).toHaveBeenCalledWith(expect.anything(), "src-1", {
      enabled: true,
      apply_to_existing: true,
    });
  });
});

// --- LUM-721 — the explicit way back ----------------------------------------

describe("re-file after an undo", () => {
  it("offers File again on a reversed row, and no Undo", async () => {
    api.fetchFiledAttachments.mockResolvedValue({
      rows: [row({ reversed_at: "2026-07-26T10:00:00Z" })],
      total: 1,
    });

    render(<FiledFromEmailList client={{} as never} />);

    expect(
      await screen.findByRole("button", { name: /file invoice\.pdf again/i }),
    ).toBeTruthy();
    expect(screen.queryByRole("button", { name: /undo/i })).toBeNull();
  });

  it("offers no File again on a skip — nothing was ever filed", async () => {
    api.fetchFiledAttachments.mockResolvedValue({
      rows: [row({ skipped_reason: "no_extractor", reversed_at: null })],
      total: 1,
    });

    render(<FiledFromEmailList client={{} as never} />);

    await screen.findByTestId("filed-skip-reason");
    expect(screen.queryByRole("button", { name: /again/i })).toBeNull();
  });

  it("restores the row once the server confirms", async () => {
    const user = userEvent.setup();
    api.fetchFiledAttachments.mockResolvedValue({
      rows: [row({ reversed_at: "2026-07-26T10:00:00Z" })],
      total: 1,
    });
    api.refileAttachment.mockResolvedValue({ outcome: "refiled" });

    render(<FiledFromEmailList client={{} as never} />);
    await user.click(await screen.findByRole("button", { name: /again/i }));

    // Not optimistic, unlike undo: re-filing re-runs the fetch, and the original
    // message may have been deleted since.
    expect(await screen.findByRole("button", { name: /undo/i })).toBeTruthy();
    expect(screen.getByTestId("filed-status")).toHaveTextContent(/back in your library/i);
  });

  it("explains a re-file that could not happen, and leaves the row reversed", async () => {
    const user = userEvent.setup();
    api.fetchFiledAttachments.mockResolvedValue({
      rows: [row({ reversed_at: "2026-07-26T10:00:00Z" })],
      total: 1,
    });
    api.refileAttachment.mockResolvedValue({ outcome: "unavailable" });

    render(<FiledFromEmailList client={{} as never} />);
    await user.click(await screen.findByRole("button", { name: /again/i }));

    expect(await screen.findByTestId("filed-status")).toHaveTextContent(
      /original email may have been deleted/i,
    );
    expect(screen.getByRole("button", { name: /again/i })).toBeTruthy();
  });

  it("says so when the same file already arrived in another message", async () => {
    const user = userEvent.setup();
    api.fetchFiledAttachments.mockResolvedValue({
      rows: [row({ reversed_at: "2026-07-26T10:00:00Z" })],
      total: 1,
    });
    api.refileAttachment.mockResolvedValue({ outcome: "duplicate" });

    render(<FiledFromEmailList client={{} as never} />);
    await user.click(await screen.findByRole("button", { name: /again/i }));

    expect(await screen.findByTestId("filed-status")).toHaveTextContent(/already filed/i);
  });
});

// --- LUM-727: reconsidering a decline ----------------------------------------
//
// The review list tells the user "you can undo this from Filed from email". If
// declined rows are hidden here with no way to show them, that sentence points
// at nothing — which is worse than not offering the undo at all.

describe("declined attachments", () => {
  it("hides them by default — this surface is about what WAS filed", async () => {
    api.fetchFiledAttachments.mockResolvedValue({ rows: [row()], total: 1 });

    render(<FiledFromEmailList client={{} as never} />);

    await waitFor(() => expect(api.fetchFiledAttachments).toHaveBeenCalled());
    expect(api.fetchFiledAttachments.mock.calls[0][3]).toBe(false);
  });

  it("asks the server for them when the user opts in", async () => {
    api.fetchFiledAttachments.mockResolvedValue({ rows: [row()], total: 1 });
    const user = userEvent.setup();

    render(<FiledFromEmailList client={{} as never} />);
    await user.click(
      await screen.findByRole("checkbox", { name: /show attachments you chose not to file/i }),
    );

    await waitFor(() =>
      expect(api.fetchFiledAttachments.mock.calls.at(-1)?.[3]).toBe(true),
    );
  });

  it("offers the toggle even when nothing is filed yet", async () => {
    // Exactly where a user who just declined everything arrives looking for it.
    api.fetchFiledAttachments.mockResolvedValue({ rows: [], total: 0 });

    render(<FiledFromEmailList client={{} as never} />);

    expect(await screen.findByTestId("filed-empty")).toBeTruthy();
    expect(
      screen.getByRole("checkbox", { name: /show attachments you chose not to file/i }),
    ).toBeTruthy();
  });

  it("offers Reconsider on a declined row", async () => {
    api.fetchFiledAttachments.mockResolvedValue({
      rows: [row({ skipped_reason: "declined" })],
      total: 1,
    });

    render(<FiledFromEmailList client={{} as never} />);

    expect(
      await screen.findByRole("button", { name: /reconsider invoice\.pdf/i }),
    ).toBeTruthy();
  });

  it("offers Reconsider on NO other skip reason", async () => {
    // Every other reason is the sweep's verdict about the file itself. Offering
    // to reconsider "no text could be read" asks a question nobody can answer.
    api.fetchFiledAttachments.mockResolvedValue({
      rows: [row({ skipped_reason: "no_extractor" })],
      total: 1,
    });

    render(<FiledFromEmailList client={{} as never} />);

    await waitFor(() => expect(screen.getByTestId("filed-list")).toBeTruthy());
    expect(screen.queryByRole("button", { name: /reconsider/i })).toBeNull();
  });

  it("puts the attachment back in the review list", async () => {
    api.fetchFiledAttachments.mockResolvedValue({
      rows: [row({ skipped_reason: "declined" })],
      total: 1,
    });
    api.undoDecline.mockResolvedValue({ restored: true });
    const user = userEvent.setup();

    render(<FiledFromEmailList client={{} as never} />);
    await user.click(await screen.findByRole("button", { name: /reconsider invoice\.pdf/i }));

    await waitFor(() =>
      expect(api.undoDecline).toHaveBeenCalledWith({} as never, "email:s:1", 0),
    );
    expect(screen.getByTestId("filed-status").textContent).toMatch(/review list/i);
  });

  it("keeps the row when the server did not restore it", async () => {
    // Not optimistic: undoing a decline DELETES the verdict row, so a failure
    // means the row is still there and still declined.
    api.fetchFiledAttachments.mockResolvedValue({
      rows: [row({ skipped_reason: "declined" })],
      total: 1,
    });
    api.undoDecline.mockResolvedValue({ restored: false });
    const user = userEvent.setup();

    render(<FiledFromEmailList client={{} as never} />);
    await user.click(await screen.findByRole("button", { name: /reconsider invoice\.pdf/i }));

    await waitFor(() =>
      expect(screen.getByTestId("filed-status").textContent).toMatch(/could not put/i),
    );
    expect(screen.getByTestId("filed-row-1")).toBeTruthy();
  });

  it("explains a decline as the user's own choice, not a failure", async () => {
    api.fetchFiledAttachments.mockResolvedValue({
      rows: [row({ skipped_reason: "declined" })],
      total: 1,
    });

    render(<FiledFromEmailList client={{} as never} />);

    expect((await screen.findByTestId("filed-skip-reason")).textContent).toMatch(
      /you chose not to file/i,
    );
  });
});
