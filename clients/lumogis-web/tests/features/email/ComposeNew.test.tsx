// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Compose a new message (LUM-830; ADR-213).
//
// A reply inherits its mailbox from the message it answers, so until now no
// surface in the product had to CHOOSE one. A composed message has no
// originating signal, and picking silently is how someone sends a personal
// message from a work address — so the choice is visible before sending, and
// only mailboxes that can actually send are offered.
//
// The subtle half is the signature. LUM-817 seeds it into the visible body and
// its effect now re-runs when the From mailbox changes, so the naive version
// appends the second mailbox's signature under the first.

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { EmailComposer, type ComposerApi } from "../../../src/features/email/EmailComposer";

const WORK = { id: "mb-work", name: "Work" };
const HOME = { id: "mb-home", name: "Personal" };

function makeApi(overrides: Partial<ComposerApi> = {}): ComposerApi {
  return {
    createProposal: vi.fn(async () => ({ proposal_id: 1, first_contact: false })),
    approveProposal: vi.fn(async () => ({})),
    executeProposal: vi.fn(async () => ({ success: true, error: null, code: null })),
    cancelProposal: vi.fn(async () => ({})),
    ...overrides,
  };
}

function renderCompose(api: ComposerApi, mailboxes = [WORK, HOME]) {
  return render(
    <EmailComposer open mode="compose" mailboxes={mailboxes} api={api} onClose={vi.fn()} />,
  );
}

// ---------------------------------------------------------------------------
// The From choice
// ---------------------------------------------------------------------------

describe("compose-new — choosing the mailbox (LUM-830)", () => {
  it("offers a choice when the user can send from more than one mailbox", () => {
    renderCompose(makeApi());

    const select = screen.getByLabelText("From") as HTMLSelectElement;

    expect(select.value).toBe(WORK.id);
    expect(screen.getByRole("option", { name: "Personal" })).toBeInTheDocument();
  });

  it("states the mailbox rather than offering a choice when there is only one", () => {
    // ADR-213 asks for the sending mailbox to be VISIBLE, not merely correct —
    // so the row still renders. A one-option select would be a control that
    // cannot do anything.
    renderCompose(makeApi(), [WORK]);

    expect(screen.getByText("Work")).toBeInTheDocument();
    expect(screen.queryByLabelText("From")).toBeNull();
  });

  it("sends from the mailbox the user chose, not the default", async () => {
    // The whole point. If the payload kept taking the host's `sourceId` this
    // would still pass every render test and send from the wrong account.
    const api = makeApi();
    renderCompose(api);

    await userEvent.selectOptions(screen.getByLabelText("From"), HOME.id);
    await userEvent.type(screen.getByLabelText("To"), "bob@x.com");
    await userEvent.type(screen.getByLabelText("Subject"), "Hello");
    await userEvent.type(screen.getByLabelText("Message"), "Hi Bob");
    await userEvent.click(screen.getByRole("button", { name: /review/i }));

    await waitFor(() => expect(api.createProposal).toHaveBeenCalled());
    const [action, payload] = (api.createProposal as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(action).toBe("email_compose");
    expect(payload.source_id).toBe(HOME.id);
  });

  it("names the chosen mailbox on the confirm card instead of 'your address'", async () => {
    renderCompose(makeApi());

    await userEvent.selectOptions(screen.getByLabelText("From"), HOME.id);
    await userEvent.type(screen.getByLabelText("To"), "bob@x.com");
    await userEvent.type(screen.getByLabelText("Subject"), "Hello");
    await userEvent.click(screen.getByRole("button", { name: /review/i }));

    expect(await screen.findByText("Personal")).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// The signature, which now has to survive a mailbox change
// ---------------------------------------------------------------------------

describe("compose-new — signature follows the mailbox (LUM-830)", () => {
  function signatureApi() {
    return makeApi({
      fetchSignature: vi.fn(async (id: string) => (id === WORK.id ? "Alice — Work" : "Alice")),
    });
  }

  it("swaps the signature instead of stacking a second one", async () => {
    // The defect this exists for: the LUM-817 effect appends when the body does
    // not already contain the signature, and it now re-runs on a mailbox
    // change — so the naive version leaves BOTH signatures in the message.
    renderCompose(signatureApi());

    const body = (await screen.findByLabelText("Message")) as HTMLTextAreaElement;
    await waitFor(() => expect(body.value).toContain("Alice — Work"));

    await userEvent.selectOptions(screen.getByLabelText("From"), HOME.id);

    await waitFor(() => expect(body.value).not.toContain("Alice — Work"));
    expect(body.value).toContain("Alice");
  });

  it("swaps the signature without disturbing text the user already wrote", async () => {
    renderCompose(signatureApi());

    const body = (await screen.findByLabelText("Message")) as HTMLTextAreaElement;
    await waitFor(() => expect(body.value).toContain("Alice — Work"));
    await userEvent.type(body, "Morning Bob,");

    await userEvent.selectOptions(screen.getByLabelText("From"), HOME.id);

    await waitFor(() => expect(body.value).not.toContain("Alice — Work"));
    expect(body.value).toContain("Morning Bob,");
  });


  it("clears the old signature when the new mailbox has none", async () => {
    // The failure this guards is a send, not a render: Work's signature —
    // possibly a job title and a phone number — would go out on a message the
    // payload says came from Personal. An empty signature is a real setting,
    // not an absent one.
    renderCompose(
      makeApi({
        fetchSignature: vi.fn(async (id: string) => (id === WORK.id ? "Alice — Acme Ltd" : "")),
      }),
    );

    const body = (await screen.findByLabelText("Message")) as HTMLTextAreaElement;
    await waitFor(() => expect(body.value).toContain("Alice — Acme Ltd"));

    await userEvent.selectOptions(screen.getByLabelText("From"), HOME.id);

    await waitFor(() => expect(body.value).not.toContain("Acme Ltd"));
  });

  it("treats a signature containing $ as text, not as a substitution pattern", async () => {
    // `String.replace` reads `$&`, `` $` ``, `$'` and `$$` in the REPLACEMENT.
    // A signature quoting a price is enough to mangle itself.
    renderCompose(
      makeApi({
        fetchSignature: vi.fn(async (id: string) =>
          id === WORK.id ? "Work sig" : "Quotes from $5 — 100$$ deals",
        ),
      }),
    );

    const body = (await screen.findByLabelText("Message")) as HTMLTextAreaElement;
    await waitFor(() => expect(body.value).toContain("Work sig"));

    await userEvent.selectOptions(screen.getByLabelText("From"), HOME.id);

    await waitFor(() => expect(body.value).toContain("Quotes from $5 — 100$$ deals"));
  });

  it("leaves an edited signature alone rather than overwriting the edit", async () => {
    // Once the block no longer matches verbatim the user has made it theirs.
    // Replacing it would discard an edit they can no longer recover.
    renderCompose(signatureApi());

    const body = (await screen.findByLabelText("Message")) as HTMLTextAreaElement;
    await waitFor(() => expect(body.value).toContain("Alice — Work"));
    await userEvent.clear(body);
    await userEvent.type(body, "Alice, on my mobile");

    await userEvent.selectOptions(screen.getByLabelText("From"), HOME.id);

    await waitFor(() => expect(body.value).toContain("Alice, on my mobile"));
  });
});
