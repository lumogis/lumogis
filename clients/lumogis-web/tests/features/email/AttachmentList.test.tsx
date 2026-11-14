// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-695 read side. Two things this file exists to stop regressing:
//
//   * `available: false` and an empty list must render differently. A
//     metadata-mode mailbox never fetched bodies, so "no attachments" would be
//     a lie about a message that may well have three.
//   * The download must go through the authenticated client. Auth is an
//     in-memory bearer token, so a plain `<a href>` navigation carries no
//     credentials and 401s for every user once AUTH_ENABLED is on — a bug that
//     is invisible in a dev build where auth is off.

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "../../../src/api/client";
import { AttachmentList } from "../../../src/features/email/AttachmentList";

const downloadAttachment = vi.fn();
const acceptCandidates = vi.fn();

vi.mock("../../../src/api/emailAttachments", async () => {
  const actual = await vi.importActual<typeof import("../../../src/api/emailAttachments")>(
    "../../../src/api/emailAttachments",
  );
  return { ...actual, downloadAttachment: (...args: unknown[]) => downloadAttachment(...args) };
});

vi.mock("../../../src/api/emailAttachmentIngest", async () => {
  const actual = await vi.importActual<
    typeof import("../../../src/api/emailAttachmentIngest")
  >("../../../src/api/emailAttachmentIngest");
  return { ...actual, acceptCandidates: (...args: unknown[]) => acceptCandidates(...args) };
});

const ROWS = [
  { part_index: 0, filename: "invoice.pdf", mime_type: "application/pdf", size_bytes: 245_000 },
  { part_index: 1, filename: "photo.png", mime_type: "image/png", size_bytes: 1_048_576 },
];

const client = {} as never;

beforeEach(() => {
  downloadAttachment.mockReset();
  downloadAttachment.mockResolvedValue(undefined);
  acceptCandidates.mockReset();
  acceptCandidates.mockResolvedValue({ accepted: 1, skipped: [] });
});

describe("AttachmentList", () => {
  it("lists each attachment with a size", () => {
    render(<AttachmentList signalId="sig-1" rows={ROWS} available client={client} />);

    expect(screen.getByText("invoice.pdf")).toBeInTheDocument();
    expect(screen.getByText("photo.png")).toBeInTheDocument();
    expect(screen.getByText("239 KB")).toBeInTheDocument();
    expect(screen.getByText("1.0 MB")).toBeInTheDocument();
  });

  it("downloads through the authenticated client, not a bare link", async () => {
    // A plain <a href> would send no Authorization header — 401 for every user
    // under AUTH_ENABLED=true, and silently fine in a dev build without auth.
    render(<AttachmentList signalId="sig-1" rows={ROWS} available client={client} />);

    await userEvent.click(screen.getByRole("button", { name: "photo.png" }));

    await waitFor(() =>
      expect(downloadAttachment).toHaveBeenCalledWith(client, "sig-1", 1, "photo.png"),
    );
  });

  it("renders no anchor elements for attachments", () => {
    render(<AttachmentList signalId="sig-1" rows={ROWS} available client={client} />);

    expect(screen.queryByRole("link")).toBeNull();
  });

  it("keeps 503 and 410 distinct — one is retryable and the other never is", async () => {
    downloadAttachment.mockRejectedValueOnce(new ApiError(503, "down"));
    const { rerender } = render(
      <AttachmentList signalId="sig-1" rows={ROWS} available client={client} />,
    );

    await userEvent.click(screen.getByRole("button", { name: "invoice.pdf" }));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent(/try again/i));

    downloadAttachment.mockRejectedValueOnce(new ApiError(410, "gone"));
    rerender(<AttachmentList signalId="sig-1" rows={ROWS} available client={client} />);
    await userEvent.click(screen.getByRole("button", { name: "invoice.pdf" }));
    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent(/no longer in the mailbox/i),
    );
  });

  it("names the blocked-type and too-large refusals distinctly", async () => {
    downloadAttachment.mockRejectedValueOnce(new ApiError(415, "blocked"));
    const { rerender } = render(
      <AttachmentList signalId="sig-1" rows={ROWS} available client={client} />,
    );

    await userEvent.click(screen.getByRole("button", { name: "invoice.pdf" }));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent(/isn’t served/i));

    downloadAttachment.mockRejectedValueOnce(new ApiError(413, "too big"));
    rerender(<AttachmentList signalId="sig-1" rows={ROWS} available client={client} />);
    await userEvent.click(screen.getByRole("button", { name: "invoice.pdf" }));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent(/too large/i));
  });

  it("refuses clearly when the message identity is ambiguous", async () => {
    // Serving a plausible-but-wrong file would be worse than any error.
    downloadAttachment.mockRejectedValueOnce(new ApiError(409, "ambiguous"));
    render(<AttachmentList signalId="sig-1" rows={ROWS} available client={client} />);

    await userEvent.click(screen.getByRole("button", { name: "invoice.pdf" }));

    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent(/couldn’t confirm which message/i),
    );
  });

  it("says attachments are UNAVAILABLE for a metadata-mode mailbox, not absent", () => {
    render(<AttachmentList signalId="sig-1" rows={[]} available={false} client={client} />);

    expect(screen.getByTestId("attachments-unavailable")).toHaveTextContent(
      /need full-ingest mode/i,
    );
  });

  it("renders nothing when the mailbox DID look and there were none", () => {
    const { container } = render(
      <AttachmentList signalId="sig-1" rows={[]} available client={client} />,
    );

    expect(container).toBeEmptyDOMElement();
    expect(screen.queryByTestId("attachments-unavailable")).toBeNull();
  });

  it("states the fetch-on-demand cost rather than hiding it", () => {
    render(<AttachmentList signalId="sig-1" rows={ROWS} available client={client} />);

    expect(screen.getByText(/open from your mailbox/i)).toBeInTheDocument();
  });

  it("shows a loading state before the rows arrive", () => {
    render(<AttachmentList signalId="sig-1" rows={[]} available loading client={client} />);

    expect(screen.getByRole("status")).toHaveTextContent(/loading attachments/i);
  });

  it("shows an error state when the list could not be loaded", () => {
    render(<AttachmentList signalId="sig-1" rows={[]} available error="boom" client={client} />);

    expect(screen.getByRole("alert")).toHaveTextContent(/couldn’t load attachments/i);
  });

  it("does not report unavailable while still loading", () => {
    // Ordering matters: a slow fetch must not flash the metadata-mode message.
    render(
      <AttachmentList signalId="sig-1" rows={[]} available={false} loading client={client} />,
    );

    expect(screen.queryByTestId("attachments-unavailable")).toBeNull();
  });
});

// --- LUM-727: filing one attachment from the message itself ------------------
//
// Nothing is filed automatically any more, so this is the shortcut for the
// moment a user is already looking at the invoice and knows they want it —
// without a trip to the review list.

describe("filing to the library", () => {
  it("offers nothing by default, so the component stays safe where it is already mounted", () => {
    render(
      <AttachmentList signalId="sig-1" rows={ROWS} available client={client} />,
    );

    expect(screen.queryByRole("button", { name: /file .* to library/i })).toBeNull();
  });

  it("files the attachment it was asked about, by its own part index", async () => {
    const user = userEvent.setup();
    render(
      <AttachmentList signalId="sig-1" rows={ROWS} available client={client} canFile />,
    );

    await user.click(screen.getByRole("button", { name: /file photo\.png to library/i }));

    await waitFor(() =>
      expect(acceptCandidates).toHaveBeenCalledWith(client, [
        { signal_id: "sig-1", part_index: 1 },
      ]),
    );
  });

  it("confirms, and stops offering the button for what it filed", async () => {
    const user = userEvent.setup();
    render(
      <AttachmentList signalId="sig-1" rows={ROWS} available client={client} canFile />,
    );

    await user.click(screen.getByRole("button", { name: /file invoice\.pdf to library/i }));

    expect(await screen.findByTestId("attachment-filed")).toBeTruthy();
    expect(
      screen.queryByRole("button", { name: /file invoice\.pdf to library/i }),
    ).toBeNull();
  });

  it("reports an ordinary non-filing as an explanation, not an error", async () => {
    // `duplicate` means the file is already there — which is what the user
    // wanted. Showing it as a failure would send them looking for a problem
    // that does not exist.
    acceptCandidates.mockResolvedValue({
      accepted: 0,
      skipped: [{ signal_id: "sig-1", part_index: 0, reason: "duplicate" }],
    });
    const user = userEvent.setup();
    render(
      <AttachmentList signalId="sig-1" rows={ROWS} available client={client} canFile />,
    );

    await user.click(screen.getByRole("button", { name: /file invoice\.pdf to library/i }));

    expect((await screen.findByTestId("attachment-file-status")).textContent).toContain(
      "Already in your library",
    );
  });

  it("does not claim a filing that failed", async () => {
    acceptCandidates.mockRejectedValue(new Error("boom"));
    const user = userEvent.setup();
    render(
      <AttachmentList signalId="sig-1" rows={ROWS} available client={client} canFile />,
    );

    await user.click(screen.getByRole("button", { name: /file invoice\.pdf to library/i }));

    await waitFor(() => expect(screen.getByTestId("attachment-file-status")).toBeTruthy());
    expect(screen.queryByTestId("attachment-filed")).toBeNull();
  });

  it("still downloads, because filing is an addition and not a replacement", async () => {
    const user = userEvent.setup();
    render(
      <AttachmentList signalId="sig-1" rows={ROWS} available client={client} canFile />,
    );

    await user.click(screen.getByRole("button", { name: "invoice.pdf" }));

    await waitFor(() => expect(downloadAttachment).toHaveBeenCalled());
  });
});
