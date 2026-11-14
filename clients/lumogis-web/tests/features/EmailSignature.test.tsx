// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Per-mailbox signature in the composer (LUM-817).
//
// The headline test is the interaction, not the feature. The reply-draft
// anti-clobber fills only an EMPTY body; seeding a signature makes the body
// non-empty, so a naive implementation lets a signature silently switch OFF the
// AI draft — one feature disabling another with no error anywhere.

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { EmailComposer, type ComposerApi } from "../../src/features/email/EmailComposer";

const SIG = "Alice Smith\n07700 900000";

function makeApi(overrides: Partial<ComposerApi> = {}): ComposerApi {
  return {
    createProposal: vi.fn(async () => ({ proposal_id: 1, first_contact: false })),
    approveProposal: vi.fn(async () => ({})),
    executeProposal: vi.fn(async () => ({ success: true, error: null, code: null })),
    cancelProposal: vi.fn(async () => ({})),
    fetchReplyDraft: vi.fn(async () => ""),
    ...overrides,
  };
}

function renderReply(api: ComposerApi, extra = {}) {
  return render(
    <EmailComposer
      open
      mode="reply"
      signalId="sig-1"
      sourceId="mailbox-1"
      replyRecipient="bob@x.com"
      originalSubject="Lunch?"
      fromAddress="alice@gmail.com"
      api={api}
      onClose={vi.fn()}
      {...extra}
    />,
  );
}

describe("composer signature (LUM-817)", () => {
  it("seeds the signature into the visible body so it can be edited", async () => {
    renderReply(makeApi({ fetchSignature: vi.fn(async () => SIG) }));

    const body = await screen.findByLabelText("Message");
    await waitFor(() => expect((body as HTMLTextAreaElement).value).toContain("Alice Smith"));
  });

  it("still lets the AI draft populate an otherwise pristine body", async () => {
    // THE regression this guards. Without signature-aware pristineness the
    // draft never lands, because the body is no longer "".
    renderReply(
      makeApi({
        fetchSignature: vi.fn(async () => SIG),
        fetchReplyDraft: vi.fn(async () => "Suggested reply."),
      }),
    );

    const body = await screen.findByLabelText("Message");
    await waitFor(() => {
      const v = (body as HTMLTextAreaElement).value;
      expect(v).toContain("Suggested reply.");
      expect(v).toContain("Alice Smith");
    });
  });

  it("places the draft above the signature, not after it", async () => {
    renderReply(
      makeApi({
        fetchSignature: vi.fn(async () => SIG),
        fetchReplyDraft: vi.fn(async () => "Suggested reply."),
      }),
    );

    const body = await screen.findByLabelText("Message");
    await waitFor(() => {
      const v = (body as HTMLTextAreaElement).value;
      expect(v.indexOf("Suggested reply.")).toBeLessThan(v.indexOf("Alice Smith"));
    });
  });

  it("does not overwrite what the user already typed", async () => {
    let resolveSig: (s: string) => void = () => {};
    renderReply(
      makeApi({ fetchSignature: vi.fn(() => new Promise<string>((r) => (resolveSig = r))) }),
    );

    const body = await screen.findByLabelText("Message");
    await userEvent.type(body, "Typed first.");
    resolveSig(SIG);

    await new Promise((r) => setTimeout(r, 50));
    expect((body as HTMLTextAreaElement).value).toContain("Typed first.");
  });

  it("composes normally when the mailbox has no signature", async () => {
    renderReply(makeApi({ fetchSignature: vi.fn(async () => "") }));

    const body = await screen.findByLabelText("Message");
    await waitFor(() => expect((body as HTMLTextAreaElement).value).toBe(""));
  });

  it("a failed signature fetch never blocks composing", async () => {
    renderReply(makeApi({ fetchSignature: vi.fn(async () => Promise.reject(new Error("500"))) }));

    const body = await screen.findByLabelText("Message");
    expect(body).toBeTruthy();
    await userEvent.type(body, "Still typable.");
    expect((body as HTMLTextAreaElement).value).toContain("Still typable.");
  });

  it("works when the caller supplies no fetchSignature at all", async () => {
    renderReply(makeApi());

    const body = await screen.findByLabelText("Message");
    expect((body as HTMLTextAreaElement).value).toBe("");
  });
});
