// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { SendConfirmCard } from "../../../src/features/email/SendConfirmCard";

function baseProps() {
  return {
    to: "bob@x.com",
    fromAddress: "alice@gmail.com",
    subject: "Re: Lunch?",
    body: "Sounds good.",
    firstContact: false,
    status: "idle" as const,
    onSend: vi.fn(),
    onSaveDraft: vi.fn(),
    onBack: vi.fn(),
  };
}

describe("SendConfirmCard", () => {
  it("shows the first-contact banner only when firstContact", () => {
    const { rerender } = render(<SendConfirmCard {...baseProps()} firstContact={false} />);
    expect(screen.queryByText(/haven’t been emailed/i)).toBeNull();
    rerender(<SendConfirmCard {...baseProps()} firstContact={true} />);
    const notice = screen.getByText(/haven’t been emailed from this mailbox before/i);
    expect(notice).toBeInTheDocument();
    // LUM-698: the notice must NOT name a recipient. The flag can be raised by
    // any recipient including a cc/bcc, and the backend returns only a boolean
    // (_annotate_send runs before the ownership gate), so naming `to` would be
    // an outright misattribution.
    expect(notice.textContent).not.toContain("bob@x.com");
  });

  it("re-displays the exact recipient, from-address, and body", () => {
    render(<SendConfirmCard {...baseProps()} />);
    expect(screen.getByText(/Send this to/i)).toHaveTextContent(
      "Send this to bob@x.com, from alice@gmail.com?",
    );
    expect(screen.getByText("Sounds good.")).toBeInTheDocument();
  });

  it("hides Save-to-Drafts and shows a message on no_drafts_folder", () => {
    render(
      <SendConfirmCard {...baseProps()} status="failed" failureCode="no_drafts_folder" />,
    );
    expect(screen.queryByRole("button", { name: /save to drafts/i })).toBeNull();
    expect(screen.getByText(/no Drafts folder/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /retry/i })).toBeInTheDocument();
  });

  it("labels the primary button Retry after a failure", () => {
    render(<SendConfirmCard {...baseProps()} status="failed" failureMessage="boom" />);
    expect(screen.getByRole("button", { name: /retry/i })).toBeInTheDocument();
    // Save-to-Drafts fallback is still offered when the folder exists.
    expect(screen.getByRole("button", { name: /save to drafts/i })).toBeInTheDocument();
  });

  it("shows Sent ✓ and no action buttons on a real send", () => {
    render(<SendConfirmCard {...baseProps()} status="sent" />);
    expect(screen.getByText("Sent ✓")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /send/i })).toBeNull();
  });

  it("shows Saved to Drafts ✓ — never Sent ✓ — after Save-to-Drafts", () => {
    render(<SendConfirmCard {...baseProps()} status="sent" savedDraft />);
    expect(screen.getByText("Saved to Drafts ✓")).toBeInTheDocument();
    expect(screen.queryByText("Sent ✓")).toBeNull();
  });

  it("wires the callbacks", async () => {
    const p = baseProps();
    const user = userEvent.setup();
    render(<SendConfirmCard {...p} />);
    await user.click(screen.getByRole("button", { name: "Send" }));
    await user.click(screen.getByRole("button", { name: /save to drafts/i }));
    await user.click(screen.getByRole("button", { name: /back/i }));
    expect(p.onSend).toHaveBeenCalledOnce();
    expect(p.onSaveDraft).toHaveBeenCalledOnce();
    expect(p.onBack).toHaveBeenCalledOnce();
  });
});

// --------------------------------------------------------------------------- //
// LUM-698 — every recipient of an irreversible send is visible at the checkpoint
// --------------------------------------------------------------------------- //
describe("SendConfirmCard — cc/bcc + forward (LUM-698)", () => {
  it("lists cc and bcc recipients", () => {
    render(
      <SendConfirmCard
        {...baseProps()}
        cc={["carol@y.com", "dan@y.com"]}
        bcc={["eve@z.com"]}
      />,
    );
    expect(screen.getByTestId("confirm-cc")).toHaveTextContent("Cc: carol@y.com, dan@y.com");
    expect(screen.getByTestId("confirm-bcc")).toHaveTextContent("Bcc: eve@z.com");
  });

  it("omits the cc/bcc rows entirely when there are none", () => {
    render(<SendConfirmCard {...baseProps()} cc={[]} bcc={[]} />);
    expect(screen.queryByTestId("confirm-cc")).toBeNull();
    expect(screen.queryByTestId("confirm-bcc")).toBeNull();
  });

  it("ignores blank-only entries so an empty input never renders a row", () => {
    render(<SendConfirmCard {...baseProps()} cc={["  "]} bcc={[""]} />);
    expect(screen.queryByTestId("confirm-cc")).toBeNull();
    expect(screen.queryByTestId("confirm-bcc")).toBeNull();
  });

  it("says Forward — not Send — in the prompt for a forward", () => {
    render(<SendConfirmCard {...baseProps()} mode="forward" />);
    expect(screen.getByText(/Forward this to/i)).toHaveTextContent(
      "Forward this to bob@x.com, from alice@gmail.com?",
    );
  });

  it("re-displays the composed body verbatim, quote included", () => {
    const composed = "See below.\n\n---------- Forwarded message ----------\nFrom: x@y.com";
    render(<SendConfirmCard {...baseProps()} mode="forward" body={composed} />);
    // The approval checkpoint must show exactly what will be sent.
    expect(screen.getByText(/Forwarded message/)).toHaveTextContent(
      "---------- Forwarded message ----------",
    );
  });

  // LUM-695 — sending a file is not something to discover after the fact.
  it("shows a file count and total size when attachments ride along", () => {
    render(<SendConfirmCard {...baseProps()} attachmentCount={3} attachmentBytes={1_258_291} />);
    expect(screen.getByTestId("confirm-attachments")).toHaveTextContent("3 files, 1.2 MB attached");
  });

  it("says 'file' not 'files' for a single attachment", () => {
    render(<SendConfirmCard {...baseProps()} attachmentCount={1} attachmentBytes={2048} />);
    expect(screen.getByTestId("confirm-attachments")).toHaveTextContent("1 file, 2.0 KB attached");
  });

  it("shows no attachment line when there are none", () => {
    render(<SendConfirmCard {...baseProps()} />);
    expect(screen.queryByTestId("confirm-attachments")).toBeNull();
  });

  it("still shows the count when the size could not be computed", () => {
    // _attachment_summary degrades to a count-only annotation rather than
    // failing the proposal; the card must degrade with it.
    render(<SendConfirmCard {...baseProps()} attachmentCount={2} />);
    expect(screen.getByTestId("confirm-attachments")).toHaveTextContent("2 files attached");
  });
});
