// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-695 send side. The invariant under test is that the composer only ever
// holds REFERENCES: `stageAttachment` returns an id, and that id is what the
// parent puts in the action payload. Bytes never enter it.

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "../../../src/api/client";
import { AttachmentPicker } from "../../../src/features/email/AttachmentPicker";

const stageAttachment = vi.fn();
const discardStagedAttachment = vi.fn();

vi.mock("../../../src/api/emailAttachments", async () => {
  const actual = await vi.importActual<typeof import("../../../src/api/emailAttachments")>(
    "../../../src/api/emailAttachments",
  );
  return {
    ...actual,
    stageAttachment: (...args: unknown[]) => stageAttachment(...args),
    discardStagedAttachment: (...args: unknown[]) => discardStagedAttachment(...args),
  };
});

const client = {} as never;

function staged(id: string, filename = "invoice.pdf", size = 1024) {
  return { id, filename, mime_type: "application/pdf", size_bytes: size };
}

function file(name = "invoice.pdf") {
  return new File(["PDFBYTES"], name, { type: "application/pdf" });
}

beforeEach(() => {
  stageAttachment.mockReset();
  discardStagedAttachment.mockReset();
  discardStagedAttachment.mockResolvedValue(undefined);
});

describe("AttachmentPicker", () => {
  it("hands the parent a REFERENCE, never the file bytes", async () => {
    stageAttachment.mockResolvedValue(staged("staged-1"));
    const onStaged = vi.fn();
    render(
      <AttachmentPicker client={client} staged={[]} onStaged={onStaged} onRemoved={vi.fn()} />,
    );

    await userEvent.upload(screen.getByTestId("attachment-input"), file());

    await waitFor(() => expect(onStaged).toHaveBeenCalledTimes(1));
    const handed = onStaged.mock.calls[0][0];
    expect(handed).toEqual(staged("staged-1"));
    expect(Object.keys(handed)).not.toContain("data");
  });

  it("shows the file count and total size the approver will see", () => {
    render(
      <AttachmentPicker
        client={client}
        staged={[staged("a", "one.pdf", 500_000), staged("b", "two.pdf", 750_000)]}
        onStaged={vi.fn()}
        onRemoved={vi.fn()}
      />,
    );

    expect(screen.getByTestId("attachment-summary")).toHaveTextContent("2 files, 1.2 MB");
  });

  it("removes a file from the message before waiting on the server", async () => {
    // The user asked for it gone. A failed server cleanup must not leave it
    // attached to what they send — the sweep reclaims the disk regardless.
    let reject: (e: unknown) => void = () => {};
    discardStagedAttachment.mockReturnValue(new Promise((_, r) => { reject = r; }));
    const onRemoved = vi.fn();
    render(
      <AttachmentPicker
        client={client}
        staged={[staged("staged-1")]}
        onStaged={vi.fn()}
        onRemoved={onRemoved}
      />,
    );

    await userEvent.click(screen.getByRole("button", { name: /remove invoice\.pdf/i }));

    expect(onRemoved).toHaveBeenCalledWith("staged-1");
    reject(new Error("server down"));
  });

  it("distinguishes a too-big file from a full staging quota", async () => {
    stageAttachment.mockRejectedValue(new ApiError(413, "staging_quota_exceeded"));
    render(
      <AttachmentPicker client={client} staged={[]} onStaged={vi.fn()} onRemoved={vi.fn()} />,
    );

    await userEvent.upload(screen.getByTestId("attachment-input"), file());

    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent(/too many files waiting to send/i),
    );
  });

  it("reports an oversize file as an oversize file", async () => {
    stageAttachment.mockRejectedValue(new ApiError(413, "attachment_too_large"));
    render(
      <AttachmentPicker client={client} staged={[]} onStaged={vi.fn()} onRemoved={vi.fn()} />,
    );

    await userEvent.upload(screen.getByTestId("attachment-input"), file());

    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent(/too large to attach/i),
    );
  });

  it("reports an upload rate limit distinctly", async () => {
    stageAttachment.mockRejectedValue(new ApiError(429, "slow down"));
    render(
      <AttachmentPicker client={client} staged={[]} onStaged={vi.fn()} onRemoved={vi.fn()} />,
    );

    await userEvent.upload(screen.getByTestId("attachment-input"), file());

    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent(/too many uploads/i),
    );
  });

  it("disables the input once the per-message file cap is reached", () => {
    render(
      <AttachmentPicker
        client={client}
        staged={Array.from({ length: 10 }, (_, i) => staged(`s-${i}`))}
        onStaged={vi.fn()}
        onRemoved={vi.fn()}
      />,
    );

    expect(screen.getByTestId("attachment-input")).toBeDisabled();
  });

  it("stops uploading after the first failure rather than repeating it", async () => {
    stageAttachment.mockRejectedValue(new ApiError(413, "staging_quota_exceeded"));
    render(
      <AttachmentPicker client={client} staged={[]} onStaged={vi.fn()} onRemoved={vi.fn()} />,
    );

    await userEvent.upload(screen.getByTestId("attachment-input"), [file("a.pdf"), file("b.pdf")]);

    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
    expect(stageAttachment).toHaveBeenCalledTimes(1);
  });
});
