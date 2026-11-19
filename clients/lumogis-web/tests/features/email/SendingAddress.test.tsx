// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// The sending address on every send surface (LUM-831).
//
// These tests deliberately do NOT reuse `renderReply` / `renderForward` from
// EmailComposer.test.tsx, nor `baseProps()` from SendConfirmCard.test.tsx.
// All three hardcode `fromAddress="alice@gmail.com"`, so a test built on them
// asserts against a component its own setup told the answer to — it passes
// whether or not the host wires anything. Two independent critiques flagged
// that the natural, idiomatic way to write these tests is also the way that
// cannot catch the defect they were written for.
//
// The defect: the reply/forward mounts passed no `sourceId` and no `mailboxes`,
// so `chosenMailboxName` was structurally `""` there and the confirm card's
// ternary fell to a hardcoded `null` — every reply read "from your address".

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { EmailComposer, type ComposerApi } from "../../../src/features/email/EmailComposer";
import { SendConfirmCard } from "../../../src/features/email/SendConfirmCard";

const WORK = { id: "mb-work", name: "Work", address: "alice@acme.com" };
const HOME = { id: "mb-home", name: "Personal", address: "alice@fastmail.com" };

function makeApi(): ComposerApi {
  return {
    createProposal: vi.fn(async () => ({ proposal_id: 1, first_contact: false })),
    approveProposal: vi.fn(async () => ({})),
    executeProposal: vi.fn(async () => ({ success: true, error: null, code: null })),
    cancelProposal: vi.fn(async () => ({})),
  };
}

/** Reply WITHOUT a hardcoded fromAddress — the whole point of this file. */
function renderReplyBare(extra: Record<string, unknown> = {}) {
  return render(
    <EmailComposer
      open
      mode="reply"
      signalId="sig-1"
      replyRecipient="bob@x.com"
      originalSubject="Lunch?"
      api={makeApi()}
      onClose={vi.fn()}
      {...extra}
    />,
  );
}

describe("LUM-831 — the From picker names the address", () => {
  it("labels each option Name — address", async () => {
    render(
      <EmailComposer
        open
        mode="compose"
        mailboxes={[WORK, HOME]}
        api={makeApi()}
        onClose={vi.fn()}
      />,
    );
    await userEvent.click(screen.getByRole("combobox", { name: "From" }));
    expect(await screen.findByRole("option", { name: "Work — alice@acme.com" })).toBeTruthy();
    expect(await screen.findByRole("option", { name: "Personal — alice@fastmail.com" })).toBeTruthy();
  });

  it("falls back to the bare name when an address did not resolve", async () => {
    render(
      <EmailComposer
        open
        mode="compose"
        mailboxes={[WORK, { id: "mb-x", name: "Broken" }]}
        api={makeApi()}
        onClose={vi.fn()}
      />,
    );
    // Still offered — dropping it would remove a mailbox the user can genuinely
    // send from, which is the LUM-719 mistake in a new place.
    await userEvent.click(screen.getByRole("combobox", { name: "From" }));
    expect(await screen.findByRole("option", { name: "Broken" })).toBeTruthy();
  });

  it("shows the degraded note with two mailboxes and no addresses", () => {
    render(
      <EmailComposer
        open
        mode="compose"
        mailboxes={[
          { id: "a", name: "Work" },
          { id: "b", name: "Personal" },
        ]}
        addressesDegraded
        api={makeApi()}
        onClose={vi.fn()}
      />,
    );
    expect(screen.getByText(/Couldn't load sending addresses/)).toBeTruthy();
  });

  it("hides the degraded note when there is only one mailbox", () => {
    render(
      <EmailComposer
        open
        mode="compose"
        mailboxes={[{ id: "a", name: "Work" }]}
        api={makeApi()}
        onClose={vi.fn()}
      />,
    );
    expect(screen.queryByText(/Couldn't load sending addresses/)).toBeNull();
  });
});

describe("LUM-831 — reply and forward name the account too", () => {
  /** The From line only exists on the confirm step, so get there. */
  async function toConfirm(extra: Record<string, unknown>) {
    const user = userEvent.setup();
    renderReplyBare(extra);
    await user.type(screen.getByLabelText(/message/i), "Sounds good.");
    await user.click(screen.getByRole("button", { name: "Review" }));
    return screen.getByRole("group", { name: "Confirm send" });
  }

  it("uses the resolved address on a reply", async () => {
    const card = await toConfirm({ fromAddress: "alice@acme.com", fromName: "Work" });
    expect(card.textContent).toContain("alice@acme.com");
  });

  it("reads the same as compose when both address and name are known", async () => {
    // Found by /code-review: the first cut made these mutually exclusive, so a
    // reply showed a bare address while compose showed `Name — address`. The
    // confirm card must not read differently depending on which button opened
    // it, for a distinction the user cannot see.
    const card = await toConfirm({ fromAddress: "alice@acme.com", fromName: "Work" });
    expect(card.textContent).toContain("Work — alice@acme.com");
  });

  it("shows the mailbox NAME on a reply when the address is unavailable", async () => {
    // The case the ADR promises ("name-only, never disappears") and the case the
    // old wiring could not produce at all: reply mode had no path to a name, so
    // this assertion fails against the pre-LUM-831 wiring.
    const card = await toConfirm({ fromName: "Work" });
    expect(card.textContent).toContain("Work");
    expect(card.textContent).not.toContain("this mailbox");
  });

  it("never renders the retired 'your address' placeholder", async () => {
    const card = await toConfirm({ fromName: "Work" });
    expect(card.textContent).not.toContain("your address");
  });
});

describe("LUM-831 — the confirm card", () => {
  it("names the address rather than a placeholder", () => {
    render(
      <SendConfirmCard
        to="bob@x.com"
        fromAddress="Work — alice@acme.com"
        subject="Re: Lunch?"
        body="Sounds good."
        firstContact={false}
        status="idle"
        onSend={vi.fn()}
        onSaveDraft={vi.fn()}
        onBack={vi.fn()}
      />,
    );
    expect(screen.getByText("Work — alice@acme.com")).toBeTruthy();
    expect(screen.queryByText(/your address/)).toBeNull();
  });
});
