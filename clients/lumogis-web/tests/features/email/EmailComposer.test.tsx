// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { EmailComposer, type ComposerApi } from "../../../src/features/email/EmailComposer";
import { installMockMediaRecorder } from "../../helpers/mockMediaRecorder";

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
      replyRecipient="bob@x.com"
      originalSubject="Lunch?"
      fromAddress="alice@gmail.com"
      api={api}
      onClose={vi.fn()}
      {...extra}
    />,
  );
}

describe("EmailComposer", () => {
  it("populates an untouched body from the suggested draft", async () => {
    const api = makeApi({ fetchReplyDraft: vi.fn(async () => "Suggested reply.") });
    renderReply(api);
    const body = (await screen.findByLabelText("Message")) as HTMLTextAreaElement;
    await waitFor(() => expect(body.value).toBe("Suggested reply."));
  });

  it("anti-clobber: a late draft never overwrites text the user already typed", async () => {
    let resolveDraft: (v: string) => void = () => {};
    const api = makeApi({
      fetchReplyDraft: vi.fn(() => new Promise<string>((r) => (resolveDraft = r))),
    });
    const user = userEvent.setup();
    renderReply(api);
    const body = (await screen.findByLabelText("Message")) as HTMLTextAreaElement;
    await user.type(body, "my own words");
    resolveDraft("SLOW SUGGESTION"); // arrives after the user typed
    await waitFor(() => expect(api.fetchReplyDraft).toHaveBeenCalled());
    expect(body.value).toBe("my own words");
  });

  it("Send runs create → approve → execute and reaches Sent ✓", async () => {
    const api = makeApi();
    const user = userEvent.setup();
    renderReply(api);
    const body = await screen.findByLabelText("Message");
    await user.type(body, "sure");
    await user.click(screen.getByRole("button", { name: "Review" }));
    await user.click(await screen.findByRole("button", { name: "Send" }));
    await screen.findByText(/sent ✓/i);
    expect(api.createProposal).toHaveBeenCalled();
    expect(api.approveProposal).toHaveBeenCalledWith(1);
    expect(api.executeProposal).toHaveBeenCalledWith(1);
  });

  // ── LUM-793: a failed send must not be discarded silently ──────────────────
  //
  // The composed text lives ONLY in React state — there is no draft autosave — so
  // one reflexive Escape on the failure screen loses the message. It survives in
  // `action_proposals.payload` server-side, but no surface shows it (LUM-793's other
  // half), so from the user's side it is gone.

  // jsdom implements <dialog> only partially: a real "{Escape}" keypress does NOT
  // produce the `cancel` event that React's onCancel binds to, so a keyboard-driven
  // test passes vacuously — nothing fires and nothing is asserted against. Dispatch
  // the event the browser would actually emit. This tests the HANDLER and the guard,
  // which is where the defect was; the key-to-event mapping is the platform's.
  function pressEscape() {
    const dialog = document.querySelector("dialog.lumogis-composer");
    if (!dialog) throw new Error("composer dialog not in the document");
    fireEvent(dialog, new Event("cancel", { bubbles: false, cancelable: true }));
  }

  async function failASend(user: ReturnType<typeof userEvent.setup>, api: ComposerApi) {
    await user.type(await screen.findByLabelText("Message"), "a message worth keeping");
    await user.click(screen.getByRole("button", { name: "Review" }));
    await user.click(await screen.findByRole("button", { name: /^send$/i }));
    await screen.findByText(/couldn’t send/i);
    return api;
  }

  const failingApi = () =>
    makeApi({
      executeProposal: vi.fn(async () => ({
        success: false,
        error: "SMTP submission failed",
        code: "smtp_connect",
      })),
    });

  it("asks before discarding a failed send on Escape, and keeps it when refused", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    const api = failingApi();
    renderReply(api, { onClose });
    await failASend(user, api);

    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);
    pressEscape();

    expect(confirmSpy).toHaveBeenCalledTimes(1);
    expect(onClose).not.toHaveBeenCalled();
    expect(api.cancelProposal).not.toHaveBeenCalled();
    confirmSpy.mockRestore();
  });

  it("closes on Escape after a failed send when the discard is confirmed", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    const api = failingApi();
    renderReply(api, { onClose });
    await failASend(user, api);

    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    pressEscape();

    expect(confirmSpy).toHaveBeenCalledTimes(1);
    expect(onClose).toHaveBeenCalled();
    confirmSpy.mockRestore();
  });

  it("does NOT prompt on Escape when nothing has failed", async () => {
    const onClose = vi.fn();
    renderReply(makeApi(), { onClose });
    await screen.findByLabelText("Message");

    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    pressEscape();

    // The guard must be scoped to the failure state. Prompting on every close would
    // be the obvious over-correction, and is the way this fix would most plausibly
    // go wrong.
    expect(confirmSpy).not.toHaveBeenCalled();
    expect(onClose).toHaveBeenCalled();
    confirmSpy.mockRestore();
  });

  it("surfaces first_contact from the create response on the confirm card", async () => {
    const api = makeApi({
      createProposal: vi.fn(async () => ({ proposal_id: 7, first_contact: true })),
    });
    const user = userEvent.setup();
    renderReply(api);
    await user.type(await screen.findByLabelText("Message"), "hi");
    await user.click(screen.getByRole("button", { name: "Review" }));
    // LUM-698: the notice is deliberately recipient-AGNOSTIC. The flag can be
    // raised by any recipient including a cc/bcc, so naming the To address (as
    // this previously did) actively misattributed the warning. The backend
    // returns only a boolean by design, so naming the real one would mean
    // weakening that contract.
    const notice = await screen.findByText(/haven’t been emailed from this mailbox/i);
    expect(notice).toBeInTheDocument();
    expect(notice.textContent).not.toContain("bob@x.com");
  });

  it("Save-to-Drafts success reads 'Saved to Drafts ✓', not 'Sent ✓'", async () => {
    const api = makeApi();
    const user = userEvent.setup();
    renderReply(api);
    await user.type(await screen.findByLabelText("Message"), "hi");
    await user.click(screen.getByRole("button", { name: "Review" }));
    await user.click(await screen.findByRole("button", { name: /save to drafts/i }));
    expect(await screen.findByText("Saved to Drafts ✓")).toBeInTheDocument();
    expect(screen.queryByText("Sent ✓")).toBeNull();
  });

  it("remounting for a different signal (key change) starts from a blank compose", async () => {
    const api = makeApi({ fetchReplyDraft: vi.fn(async () => "") });
    const user = userEvent.setup();
    const { rerender } = render(
      <EmailComposer
        key="sig-1"
        open
        mode="reply"
        signalId="sig-1"
        replyRecipient="bob@x.com"
        originalSubject="Lunch?"
        api={api}
        onClose={vi.fn()}
      />,
    );
    await user.type(await screen.findByLabelText("Message"), "half-written");
    await user.click(screen.getByRole("button", { name: "Review" }));
    await screen.findByText(/Send this to/i); // now on the confirm screen
    // Open a different email — the parent changes the key → fresh mount.
    rerender(
      <EmailComposer
        key="sig-2"
        open
        mode="reply"
        signalId="sig-2"
        replyRecipient="carol@x.com"
        originalSubject="Dinner?"
        api={api}
        onClose={vi.fn()}
      />,
    );
    // Back on the compose screen with an empty body and the new recipient/subject.
    const body = (await screen.findByLabelText("Message")) as HTMLTextAreaElement;
    expect(body.value).toBe("");
    expect(screen.queryByText(/Send this to/i)).toBeNull();
    expect((screen.getByLabelText("Subject") as HTMLInputElement).value).toBe("Re: Dinner?");
  });

  it("Save-to-Drafts on no_drafts_folder shows the message and hides the button", async () => {
    const api = makeApi({
      executeProposal: vi.fn(async () => ({
        success: false,
        error: "no drafts",
        code: "no_drafts_folder",
      })),
    });
    const user = userEvent.setup();
    renderReply(api);
    await user.type(await screen.findByLabelText("Message"), "hi");
    await user.click(screen.getByRole("button", { name: "Review" }));
    await user.click(await screen.findByRole("button", { name: /save to drafts/i }));
    expect(await screen.findByText(/no Drafts folder/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /save to drafts/i })).toBeNull();
  });

  it("Save-to-Drafts cancels the abandoned review-created send proposal (LUM-682)", async () => {
    const api = makeApi({
      createProposal: vi
        .fn()
        // Review creates the send proposal (id 5); Save-to-Drafts creates its own (id 6).
        .mockResolvedValueOnce({ proposal_id: 5, first_contact: false })
        .mockResolvedValueOnce({ proposal_id: 6, first_contact: false }),
    });
    const user = userEvent.setup();
    renderReply(api);
    await user.type(await screen.findByLabelText("Message"), "hi");
    await user.click(screen.getByRole("button", { name: "Review" }));
    await user.click(await screen.findByRole("button", { name: /save to drafts/i }));
    await screen.findByText("Saved to Drafts ✓");
    // The lingering send proposal (id 5) is cancelled; the draft proposal (6) is not.
    expect(api.cancelProposal).toHaveBeenCalledWith(5);
  });

  it("Cancel abandons the pending send proposal and closes", async () => {
    const api = makeApi({
      createProposal: vi.fn(async () => ({ proposal_id: 9, first_contact: false })),
    });
    const onClose = vi.fn();
    const user = userEvent.setup();
    render(
      <EmailComposer
        open
        mode="reply"
        signalId="sig-1"
        replyRecipient="bob@x.com"
        originalSubject="Lunch?"
        api={api}
        onClose={onClose}
      />,
    );
    await user.type(await screen.findByLabelText("Message"), "hi");
    await user.click(screen.getByRole("button", { name: "Review" }));
    await user.click(await screen.findByRole("button", { name: /back/i }));
    // Back abandons the review proposal.
    expect(api.cancelProposal).toHaveBeenCalledWith(9);
  });

  it("Retry after a failed send builds a FRESH proposal (re-creates)", async () => {
    const execute = vi
      .fn()
      .mockResolvedValueOnce({ success: false, error: "smtp down", code: "smtp_failed" })
      .mockResolvedValueOnce({ success: true, error: null, code: null });
    const api = makeApi({ executeProposal: execute });
    const user = userEvent.setup();
    renderReply(api);
    await user.type(await screen.findByLabelText("Message"), "hi");
    await user.click(screen.getByRole("button", { name: "Review" }));
    await user.click(await screen.findByRole("button", { name: "Send" }));
    await screen.findByRole("button", { name: "Retry" });
    // One create at Review. Retry must create AGAIN (dead row can't re-execute).
    expect((api.createProposal as ReturnType<typeof vi.fn>).mock.calls.length).toBe(1);
    await user.click(screen.getByRole("button", { name: "Retry" }));
    await screen.findByText(/sent ✓/i);
    expect((api.createProposal as ReturnType<typeof vi.fn>).mock.calls.length).toBe(2);
  });

  // --- LUM-653 voice dictation ---

  it("no voice button when api.transcribeVoice is absent (bare render still works)", async () => {
    const api = makeApi();
    renderReply(api);
    await screen.findByLabelText("Message");
    expect(screen.queryByRole("button", { name: "Dictate" })).toBeNull();
  });

  it("appends the transcript to an empty body and marks it touched", async () => {
    installMockMediaRecorder();
    const api = makeApi({ transcribeVoice: vi.fn(async () => "dictated") });
    const user = userEvent.setup();
    renderReply(api);
    await screen.findByLabelText("Message");
    await user.click(screen.getByRole("button", { name: "Dictate" }));
    await user.click(screen.getByRole("button", { name: "Stop dictation" }));
    const body = (await screen.findByLabelText("Message")) as HTMLTextAreaElement;
    await waitFor(() => expect(body.value).toBe("dictated"));
  });

  it("appends the transcript after existing text with a separator", async () => {
    installMockMediaRecorder();
    const api = makeApi({ transcribeVoice: vi.fn(async () => "world") });
    const user = userEvent.setup();
    renderReply(api);
    const body = (await screen.findByLabelText("Message")) as HTMLTextAreaElement;
    await user.type(body, "hello");
    await user.click(screen.getByRole("button", { name: "Dictate" }));
    await user.click(screen.getByRole("button", { name: "Stop dictation" }));
    await waitFor(() => expect(body.value).toBe("hello world"));
  });

  it("disables Review while a transcript is in flight", async () => {
    installMockMediaRecorder();
    let resolveTx: (v: string) => void = () => {};
    const api = makeApi({
      transcribeVoice: vi.fn(() => new Promise<string>((r) => (resolveTx = r))),
    });
    const user = userEvent.setup();
    renderReply(api);
    const body = (await screen.findByLabelText("Message")) as HTMLTextAreaElement;
    await user.type(body, "typed");
    await user.click(screen.getByRole("button", { name: "Dictate" }));
    await user.click(screen.getByRole("button", { name: "Stop dictation" }));
    // While transcribing, Review is blocked so a late transcript can't diverge
    // from the proposal snapshot.
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Review" })).toBeDisabled(),
    );
    resolveTx("more");
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Review" })).toBeEnabled(),
    );
  });

  // --- LUM-684 refine ---

  it("preset refine → preview → Apply replaces the body and Undo restores it", async () => {
    const refine = vi.fn(async () => ({ text: "FORMAL VERSION", available: true }));
    const api = makeApi({ refine });
    const user = userEvent.setup();
    renderReply(api);
    const bodyEl = (await screen.findByLabelText("Message")) as HTMLTextAreaElement;
    await user.type(bodyEl, "hello there");
    await user.click(screen.getByRole("button", { name: "Formal" }));
    await screen.findByText("FORMAL VERSION"); // preview
    expect(bodyEl.value).toBe("hello there"); // non-destructive until Apply
    await user.click(screen.getByRole("button", { name: "Apply" }));
    await waitFor(() => expect(bodyEl.value).toBe("FORMAL VERSION"));
    await user.click(screen.getByRole("button", { name: "Undo" }));
    await waitFor(() => expect(bodyEl.value).toBe("hello there"));
  });

  it("Discard leaves the original body untouched", async () => {
    const api = makeApi({ refine: vi.fn(async () => ({ text: "X", available: true })) });
    const user = userEvent.setup();
    renderReply(api);
    const bodyEl = (await screen.findByLabelText("Message")) as HTMLTextAreaElement;
    await user.type(bodyEl, "keep me");
    await user.click(screen.getByRole("button", { name: "Clearer" }));
    await user.click(await screen.findByRole("button", { name: "Discard" }));
    expect(bodyEl.value).toBe("keep me");
    expect(screen.queryByText("X")).toBeNull();
  });

  it("custom instruction refine produces a preview", async () => {
    const refine = vi.fn(async () => ({ text: "APOLOGETIC", available: true }));
    const api = makeApi({ refine });
    const user = userEvent.setup();
    renderReply(api);
    await user.type(await screen.findByLabelText("Message"), "sorry");
    await user.type(
      screen.getByLabelText("Custom refine instruction"),
      "make it apologetic{Enter}",
    );
    await screen.findByText("APOLOGETIC");
    expect(refine).toHaveBeenCalledWith({
      text: "sorry",
      instruction: "make it apologetic",
    });
  });

  it("unavailable disables the row and shows a note", async () => {
    const api = makeApi({ refine: vi.fn(async () => ({ text: "", available: false })) });
    const user = userEvent.setup();
    renderReply(api);
    await user.type(await screen.findByLabelText("Message"), "hi");
    await user.click(screen.getByRole("button", { name: "Shorter" }));
    expect(await screen.findByText(/configure a local model/i)).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Formal" })).toBeDisabled(),
    );
  });

  it("a rejected refine clears the refining state and shows an error, body intact", async () => {
    const api = makeApi({
      refine: vi.fn(async () => {
        throw new Error("429");
      }),
    });
    const user = userEvent.setup();
    renderReply(api);
    const bodyEl = (await screen.findByLabelText("Message")) as HTMLTextAreaElement;
    await user.type(bodyEl, "hi");
    await user.click(screen.getByRole("button", { name: "Longer" }));
    expect(await screen.findByText(/couldn’t refine/i)).toBeInTheDocument();
    expect(bodyEl.value).toBe("hi");
    // refining cleared → chips enabled again
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Formal" })).toBeEnabled(),
    );
  });

  it("a stale refine (body changed mid-flight) drops the preview", async () => {
    let resolveRefine: (v: { text: string; available: boolean }) => void = () => {};
    const api = makeApi({
      refine: vi.fn(() => new Promise<{ text: string; available: boolean }>((r) => (resolveRefine = r))),
    });
    const user = userEvent.setup();
    renderReply(api);
    const bodyEl = (await screen.findByLabelText("Message")) as HTMLTextAreaElement;
    await user.type(bodyEl, "original");
    await user.click(screen.getByRole("button", { name: "Friendlier" }));
    await user.type(bodyEl, " and more"); // body changes while refine is in flight
    resolveRefine({ text: "STALE REWRITE", available: true });
    await waitFor(() => expect(api.refine).toHaveBeenCalled());
    expect(screen.queryByText("STALE REWRITE")).toBeNull(); // preview dropped
    expect(bodyEl.value).toBe("original and more");
  });

  it("Review is disabled while a refine is in flight", async () => {
    let resolveRefine: (v: { text: string; available: boolean }) => void = () => {};
    const api = makeApi({
      refine: vi.fn(() => new Promise<{ text: string; available: boolean }>((r) => (resolveRefine = r))),
    });
    const user = userEvent.setup();
    renderReply(api);
    await user.type(await screen.findByLabelText("Message"), "hi");
    await user.click(screen.getByRole("button", { name: "Formal" }));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Review" })).toBeDisabled(),
    );
    resolveRefine({ text: "R", available: true });
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Review" })).toBeEnabled(),
    );
  });

  it("no api.refine → no refine row (bare render still works)", async () => {
    const api = makeApi(); // no refine
    renderReply(api);
    await screen.findByLabelText("Message");
    expect(screen.queryByRole("button", { name: "Formal" })).toBeNull();
  });
});

// --------------------------------------------------------------------------- //
// LUM-698 — CC/BCC, forward mode, and the visible+editable quoted original
// --------------------------------------------------------------------------- //
function renderForward(api: ComposerApi, extra = {}) {
  return render(
    <EmailComposer
      open
      mode="forward"
      signalId="sig-1"
      replyRecipient="bob@x.com"
      originalSubject="Lunch?"
      fromAddress="alice@gmail.com"
      api={api}
      onClose={vi.fn()}
      {...extra}
    />,
  );
}

describe("EmailComposer — cc/bcc (LUM-698)", () => {
  it("keeps Cc/Bcc collapsed by default", async () => {
    renderReply(makeApi());
    const disclosure = await screen.findByTestId("cc-bcc-disclosure");
    expect((disclosure as HTMLDetailsElement).open).toBe(false);
  });

  it("omits empty cc/bcc from the payload so an unused feature changes nothing", async () => {
    const api = makeApi();
    const user = userEvent.setup();
    renderReply(api);
    await user.type(await screen.findByLabelText("Message"), "hi");
    await user.click(screen.getByRole("button", { name: "Review" }));
    await waitFor(() => expect(api.createProposal).toHaveBeenCalled());
    const [, payload] = (api.createProposal as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(payload).not.toHaveProperty("cc");
    expect(payload).not.toHaveProperty("bcc");
  });

  it("sends cc/bcc as split arrays of bare addresses", async () => {
    const api = makeApi();
    const user = userEvent.setup();
    renderReply(api);
    await user.type(await screen.findByLabelText("Message"), "hi");
    await user.type(screen.getByLabelText("Cc"), "dan@y.com, erin@y.com");
    await user.type(screen.getByLabelText("Bcc"), "eve@z.com");
    await user.click(screen.getByRole("button", { name: "Review" }));
    await waitFor(() => expect(api.createProposal).toHaveBeenCalled());
    const [, payload] = (api.createProposal as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(payload.cc).toEqual(["dan@y.com", "erin@y.com"]);
    expect(payload.bcc).toEqual(["eve@z.com"]);
  });

  it("shows cc/bcc on the confirm card — every recipient of an irreversible send", async () => {
    const user = userEvent.setup();
    renderReply(makeApi());
    await user.type(await screen.findByLabelText("Message"), "hi");
    await user.type(screen.getByLabelText("Cc"), "dan@y.com");
    await user.type(screen.getByLabelText("Bcc"), "eve@z.com");
    await user.click(screen.getByRole("button", { name: "Review" }));
    expect(await screen.findByTestId("confirm-cc")).toHaveTextContent("dan@y.com");
    expect(screen.getByTestId("confirm-bcc")).toHaveTextContent("eve@z.com");
  });
});

describe("EmailComposer — quoted original (LUM-698)", () => {
  it("does not fetch the quote in reply mode until the disclosure is expanded", async () => {
    const fetchQuote = vi.fn(async () => "On Mon, bob wrote:\n> hi");
    const user = userEvent.setup();
    renderReply(makeApi({ fetchQuote }));
    await screen.findByLabelText("Message");
    // Collapsed by default → no second IMAP round-trip on a plain reply open.
    expect(fetchQuote).not.toHaveBeenCalled();
    await user.click(screen.getByText("Quoted original"));
    await waitFor(() => expect(fetchQuote).toHaveBeenCalledWith("sig-1", "reply"));
  });

  it("fetches the quote eagerly in forward mode and opens the disclosure", async () => {
    const fetchQuote = vi.fn(async () => "---------- Forwarded message ----------\nFrom: bob");
    renderForward(makeApi({ fetchQuote }));
    await waitFor(() => expect(fetchQuote).toHaveBeenCalledWith("sig-1", "forward"));
    const disclosure = await screen.findByTestId("quote-disclosure");
    expect((disclosure as HTMLDetailsElement).open).toBe(true);
  });

  it("sends the EDITED quote, not the fetched original", async () => {
    const api = makeApi({ fetchQuote: vi.fn(async () => "> original line one\n> line two") });
    const user = userEvent.setup();
    renderForward(api);
    const quote = (await screen.findByLabelText("Quoted original")) as HTMLTextAreaElement;
    await waitFor(() => expect(quote.value).toContain("original line one"));
    await user.clear(quote);
    await user.type(quote, "> trimmed");
    await user.type(screen.getByLabelText("To"), "carol@y.com");
    await user.click(screen.getByRole("button", { name: "Review" }));
    await waitFor(() => expect(api.createProposal).toHaveBeenCalled());
    const [, payload] = (api.createProposal as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(payload.body).toContain("> trimmed");
    expect(payload.body).not.toContain("line two");
  });

  it("composes body + quote with no leading blank line when the note is empty", async () => {
    const api = makeApi({ fetchQuote: vi.fn(async () => "QUOTED") });
    const user = userEvent.setup();
    renderForward(api);
    await waitFor(() =>
      expect((screen.getByLabelText("Quoted original") as HTMLTextAreaElement).value).toBe(
        "QUOTED",
      ),
    );
    await user.type(screen.getByLabelText("To"), "carol@y.com");
    await user.click(screen.getByRole("button", { name: "Review" }));
    await waitFor(() => expect(api.createProposal).toHaveBeenCalled());
    const [, payload] = (api.createProposal as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(payload.body).toBe("QUOTED");
  });

  it("previews the COMPOSED body on the confirm card, not the note alone", async () => {
    const user = userEvent.setup();
    renderForward(makeApi({ fetchQuote: vi.fn(async () => "QUOTED-ORIGINAL") }));
    await waitFor(() =>
      expect((screen.getByLabelText("Quoted original") as HTMLTextAreaElement).value).toBe(
        "QUOTED-ORIGINAL",
      ),
    );
    await user.type(screen.getByLabelText("Message"), "fyi");
    await user.type(screen.getByLabelText("To"), "carol@y.com");
    await user.click(screen.getByRole("button", { name: "Review" }));
    // The read-checkpoint must show what is actually sent — a forward whose
    // content was invisible at approval was the gap this closes.
    const preview = await screen.findByText(/QUOTED-ORIGINAL/);
    expect(preview).toBeInTheDocument();
  });

  it("degrades to no quote block when fetchQuote is absent", async () => {
    const user = userEvent.setup();
    renderReply(makeApi());
    await user.type(await screen.findByLabelText("Message"), "hi");
    expect(screen.queryByTestId("quote-disclosure")).toBeNull();
    await user.click(screen.getByRole("button", { name: "Review" }));
    expect(await screen.findByRole("button", { name: "Send" })).toBeInTheDocument();
  });
});

describe("EmailComposer — forward mode (LUM-698)", () => {
  it("seeds a Fwd: subject and requires a recipient before Review", async () => {
    const user = userEvent.setup();
    renderForward(makeApi({ fetchQuote: vi.fn(async () => "Q") }));
    expect((await screen.findByLabelText("Subject")).getAttribute("value") ?? "").toContain(
      "Fwd: Lunch?",
    );
    await user.type(screen.getByLabelText("Message"), "fyi");
    // No `to` yet → Review stays disabled (previously compose-only).
    expect(screen.getByRole("button", { name: "Review" })).toBeDisabled();
    await user.type(screen.getByLabelText("To"), "carol@y.com");
    expect(screen.getByRole("button", { name: "Review" })).toBeEnabled();
  });

  it("an empty-note forward can still reach Review (the quote IS the message)", async () => {
    const user = userEvent.setup();
    renderForward(makeApi({ fetchQuote: vi.fn(async () => "QUOTED") }));
    await waitFor(() =>
      expect((screen.getByLabelText("Quoted original") as HTMLTextAreaElement).value).toBe(
        "QUOTED",
      ),
    );
    await user.type(screen.getByLabelText("To"), "carol@y.com");
    expect(screen.getByRole("button", { name: "Review" })).toBeEnabled();
  });

  it("sends the email_forward action with a signal_id and recipients", async () => {
    const api = makeApi({ fetchQuote: vi.fn(async () => "Q") });
    const user = userEvent.setup();
    renderForward(api);
    await user.type(screen.getByLabelText("To"), "carol@y.com");
    await user.type(screen.getByLabelText("Message"), "fyi");
    await user.click(screen.getByRole("button", { name: "Review" }));
    await waitFor(() => expect(api.createProposal).toHaveBeenCalled());
    const [name, payload] = (api.createProposal as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(name).toBe("email_forward");
    expect(payload.signal_id).toBe("sig-1");
    expect(payload.to).toEqual(["carol@y.com"]);
  });

  it("Save-to-Drafts in forward mode uses the forward-draft shape", async () => {
    const api = makeApi({ fetchQuote: vi.fn(async () => "Q") });
    const user = userEvent.setup();
    renderForward(api);
    await user.type(screen.getByLabelText("To"), "carol@y.com");
    await user.type(screen.getByLabelText("Message"), "fyi");
    await user.click(screen.getByRole("button", { name: "Review" }));
    await user.click(await screen.findByRole("button", { name: "Save to Drafts" }));
    await waitFor(() => {
      const calls = (api.createProposal as ReturnType<typeof vi.fn>).mock.calls;
      const draft = calls.find(([n]) => n === "email_save_draft");
      expect(draft).toBeTruthy();
      // signal_id + non-empty `to` — the shape that previously did not exist.
      expect(draft![1].signal_id).toBe("sig-1");
      expect(draft![1].to).toEqual(["carol@y.com"]);
    });
  });

  it("uses forward copy on the confirm card, never reply copy", async () => {
    const user = userEvent.setup();
    renderForward(makeApi({ fetchQuote: vi.fn(async () => "Q") }));
    await user.type(screen.getByLabelText("To"), "carol@y.com");
    await user.type(screen.getByLabelText("Message"), "fyi");
    await user.click(screen.getByRole("button", { name: "Review" }));
    expect(await screen.findByText(/Forward this to/i)).toBeInTheDocument();
  });
});

// --------------------------------------------------------------------------- //
// LUM-712 — forwarding the original's own attachments
// --------------------------------------------------------------------------- //
describe("EmailComposer — forwarding the original's attachments", () => {
  const ROWS = [
    { part_index: 0, filename: "invoice.pdf", mime_type: "application/pdf", size_bytes: 2048 },
    { part_index: 1, filename: "photo.png", mime_type: "image/png", size_bytes: 4096 },
  ];

  function renderForward(api: ComposerApi) {
    return render(
      <EmailComposer
        open
        mode="forward"
        signalId="sig-1"
        originalSubject="Term 2 fees"
        fromAddress="alice@gmail.com"
        api={api}
        onClose={vi.fn()}
      />,
    );
  }

  it("lists the original's attachments so the user can choose", async () => {
    const api = makeApi({
      listAttachments: vi.fn(async () => ({ rows: ROWS, available: true })),
    });
    renderForward(api);

    const box = await screen.findByTestId("forward-attachments");
    expect(box).toHaveTextContent("invoice.pdf");
    expect(box).toHaveTextContent("photo.png");
  });

  it("selects none by default — forwarding a file unnoticed is the bad surprise", async () => {
    const api = makeApi({
      listAttachments: vi.fn(async () => ({ rows: ROWS, available: true })),
    });
    renderForward(api);
    await screen.findByTestId("forward-attachments");

    for (const cb of screen.getAllByRole("checkbox")) {
      expect(cb).not.toBeChecked();
    }
  });

  it("sends the chosen originals as INDEXES, never bytes", async () => {
    const create = vi.fn(
      async (_name: string, _payload: Record<string, unknown>) => ({
        proposal_id: 1,
        first_contact: false,
      }),
    );
    const api = makeApi({
      createProposal: create,
      listAttachments: vi.fn(async () => ({ rows: ROWS, available: true })),
    });
    renderForward(api);
    await screen.findByTestId("forward-attachments");

    await userEvent.type(await screen.findByLabelText("To"), "carol@y.com");
    await userEvent.type(await screen.findByLabelText("Message"), "fyi");
    await userEvent.click(screen.getAllByRole("checkbox")[1]);
    await userEvent.click(screen.getByRole("button", { name: /review/i }));

    await waitFor(() => expect(create).toHaveBeenCalled());
    const [, payload] = create.mock.calls[0];
    expect(payload.forward_part_indexes).toEqual([1]);
    expect(JSON.stringify(payload)).not.toContain("data");
  });

  it("omits the key entirely when nothing is chosen", async () => {
    const create = vi.fn(
      async (_name: string, _payload: Record<string, unknown>) => ({
        proposal_id: 1,
        first_contact: false,
      }),
    );
    const api = makeApi({
      createProposal: create,
      listAttachments: vi.fn(async () => ({ rows: ROWS, available: true })),
    });
    renderForward(api);
    await screen.findByTestId("forward-attachments");

    await userEvent.type(await screen.findByLabelText("To"), "carol@y.com");
    await userEvent.type(await screen.findByLabelText("Message"), "fyi");
    await userEvent.click(screen.getByRole("button", { name: /review/i }));

    await waitFor(() => expect(create).toHaveBeenCalled());
    // An unused feature must leave the payload exactly as it was pre-LUM-712.
    expect(create.mock.calls[0][1]).not.toHaveProperty("forward_part_indexes");
  });

  it("shows no picker when the original has no attachments", async () => {
    const api = makeApi({
      listAttachments: vi.fn(async () => ({ rows: [], available: true })),
    });
    renderForward(api);
    await screen.findByLabelText("Message");

    expect(screen.queryByTestId("forward-attachments")).toBeNull();
  });

  it("shows no picker for a metadata-mode mailbox", async () => {
    // available:false means "we cannot know", so there is nothing to offer.
    const api = makeApi({
      listAttachments: vi.fn(async () => ({ rows: [], available: false })),
    });
    renderForward(api);
    await screen.findByLabelText("Message");

    expect(screen.queryByTestId("forward-attachments")).toBeNull();
  });

  it("does not ask for attachments when replying", async () => {
    const list = vi.fn(async () => ({ rows: ROWS, available: true }));
    renderReply(makeApi({ listAttachments: list }));
    await screen.findByLabelText("Message");

    expect(list).not.toHaveBeenCalled();
    expect(screen.queryByTestId("forward-attachments")).toBeNull();
  });
});
