// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
// LUM-642 — rich sender rendering (name preferred, address fallback, unknown).
// LUM-649 — 👍/👎 feedback wiring + delta-aware Important dot.
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { EmailSignalCard } from "../../../src/features/email/EmailSignalCard";
import type { EmailSignalRow } from "../../../src/api/emailSources";

function row(overrides: Partial<EmailSignalRow> = {}): EmailSignalRow {
  return {
    signal_id: "s1",
    source_id: "src",
    title: "Dentist appointment",
    url: "imap://src/1/1",
    category: "health",
    category_label: "Health",
    scope: "personal",
    published_at: "2026-07-20T09:00:00Z",
    content_summary: "Confirm your Thursday 3pm appointment.",
    ...overrides,
  };
}

const noop = () => {};

function renderCard(
  signal: EmailSignalRow,
  handlers: Partial<{
    onSetTriage: (id: string, status: EmailSignalRow["triage_status"], at?: string) => void;
    onClearTriage: (id: string) => void;
    onFeedback: (id: string, positive: boolean) => void;
    onReply: (signal: EmailSignalRow) => void;
    onForward: (signal: EmailSignalRow) => void;
  }> = {},
) {
  return render(
    <ul>
      <EmailSignalCard
        signal={signal}
        onSetTriage={handlers.onSetTriage ?? noop}
        onClearTriage={handlers.onClearTriage ?? noop}
        onFeedback={handlers.onFeedback ?? noop}
        onReply={handlers.onReply}
        onForward={handlers.onForward}
      />
    </ul>,
  );
}

describe("EmailSignalCard", () => {
  it("shows the display name when present", () => {
    renderCard(row({ from_display: "Dr. Alice", from_addr: "a@x.com" }));
    expect(screen.getByText("Dr. Alice")).toBeInTheDocument();
    expect(screen.getByText("Dentist appointment")).toBeInTheDocument();
    expect(screen.getByText("Health")).toBeInTheDocument();
    expect(screen.getByText(/Confirm your Thursday/)).toBeInTheDocument();
  });

  it("falls back to the address when no display name", () => {
    renderCard(row({ from_display: "", from_addr: "a@x.com" }));
    expect(screen.getByText("a@x.com")).toBeInTheDocument();
  });

  it("shows 'Unknown sender' when neither is present", () => {
    renderCard(row({ from_display: "", from_addr: "" }));
    expect(screen.getByText("Unknown sender")).toBeInTheDocument();
  });

  it("marks an important signal", () => {
    renderCard(row({ importance_score: 0.9 }));
    expect(screen.getByLabelText("Important")).toBeInTheDocument();
  });

  it("does not crash without optional fields", () => {
    renderCard(row({ published_at: null, content_summary: "", category_label: "" }));
    expect(screen.getByText("Dentist appointment")).toBeInTheDocument();
  });

  // LUM-649
  it("marks important when learned_relevance_delta pushes past the threshold", () => {
    // importance 0.5 alone is below 0.6, but +0.2 delta crosses it.
    renderCard(row({ importance_score: 0.5, learned_relevance_delta: 0.2 }));
    expect(screen.getByLabelText("Important")).toBeInTheDocument();
  });

  it("does not mark important when the delta keeps it below threshold", () => {
    renderCard(row({ importance_score: 0.5, learned_relevance_delta: 0.0 }));
    expect(screen.queryByLabelText("Important")).not.toBeInTheDocument();
  });

  it("👍 records positive feedback and does NOT change triage", () => {
    const onFeedback = vi.fn();
    const onSetTriage = vi.fn();
    renderCard(row(), { onFeedback, onSetTriage });
    fireEvent.click(screen.getByLabelText("Flag more like this"));
    expect(onFeedback).toHaveBeenCalledWith("s1", true);
    expect(onSetTriage).not.toHaveBeenCalled();
  });

  it("'Not important' both reclassifies the lane AND records negative feedback", () => {
    const onFeedback = vi.fn();
    const onSetTriage = vi.fn();
    renderCard(row(), { onFeedback, onSetTriage });
    fireEvent.click(screen.getByText("Not important"));
    expect(onSetTriage).toHaveBeenCalledWith("s1", "good_to_know");
    expect(onFeedback).toHaveBeenCalledWith("s1", false);
  });
});

// --------------------------------------------------------------------------- //
// LUM-698 — Forward affordance
// --------------------------------------------------------------------------- //
describe("EmailSignalCard — Forward (LUM-698)", () => {
  it("renders Forward and fires onForward with the signal", () => {
    const onForward = vi.fn();
    renderCard(row(), { onForward });
    fireEvent.click(screen.getByRole("button", { name: "Forward" }));
    expect(onForward).toHaveBeenCalledTimes(1);
    expect(onForward.mock.calls[0][0].signal_id).toBe("s1");
  });

  it("omits Forward when no handler is supplied", () => {
    renderCard(row());
    expect(screen.queryByRole("button", { name: "Forward" })).toBeNull();
  });

  it("offers Forward even when the sender is unknown (unlike Reply)", () => {
    // You choose the recipients, so a message whose from_addr failed to parse is
    // still forwardable — Reply is not (it has no one to reply to).
    renderCard(row({ from_addr: "" }), { onForward: vi.fn(), onReply: vi.fn() });
    expect(screen.getByRole("button", { name: "Forward" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Reply" })).toBeNull();
  });
});

// --------------------------------------------------------------------------- //
// LUM-821 — which mailbox this arrived in
// --------------------------------------------------------------------------- //
//
// The lanes are cross-mailbox, so on a multi-mailbox account a card otherwise
// gives no clue which account it belongs to — the failure mode unified inboxes
// are criticised for, where you reply from the wrong address because nothing
// told you which one you were reading.
//
// The card renders what it is GIVEN and decides nothing. Whether to give it
// anything depends on how many mailboxes the viewer has and which they own,
// neither of which is knowable from one row; that lives in `EmailInboxView` and
// is tested there.
describe("EmailSignalCard — mailbox indicator (LUM-821)", () => {
  function renderWithMailbox(mailboxLabel: string) {
    return render(
      <ul>
        <EmailSignalCard
          signal={row()}
          onSetTriage={noop}
          onClearTriage={noop}
          onFeedback={noop}
          mailboxLabel={mailboxLabel}
        />
      </ul>,
    );
  }

  it("names the mailbox when one is supplied", () => {
    renderWithMailbox("Work");

    expect(screen.getByText("Work")).toBeInTheDocument();
  });

  it("announces it as a mailbox, not as one more proper noun", () => {
    // On screen the name sits beside the sender and reads as what it is. Read
    // aloud, bare, it would be indistinguishable from a person's name.
    //
    // Asserted on TEXT CONTENT, not on `aria-label`. A `<span>` with no role is
    // `role=generic`, which prohibits naming, so a browser drops an aria-label
    // here — while `getByLabelText` still matches the attribute and reports a
    // pass. That is the shape of a test that guards nothing, so this one reads
    // what a screen reader would actually announce.
    const { container } = renderWithMailbox("Work");
    const chip = container.querySelector("[data-testid=email-card-mailbox]");

    expect(chip?.textContent).toBe("Mailbox: Work");
    expect(chip?.querySelector(".sr-only")?.textContent).toBe("Mailbox: ");
  });

  it("keeps the prefix out of the visible layout", () => {
    // The chip must read as "Work" on screen; the prefix is for the screen
    // reader only, and is hidden by a class that keeps it in the a11y tree
    // (`display:none` would remove it from both).
    const { container } = renderWithMailbox("Work");
    const hidden = container.querySelector("[data-testid=email-card-mailbox] .sr-only");

    expect(hidden).not.toBeNull();
    expect(screen.getByText("Work")).toBeInTheDocument();
  });

  it("renders nothing when the label is empty", () => {
    // The host passes "" for a single-mailbox account and for mail the viewer
    // does not own. Neither should leave an empty element behind.
    const { container } = renderWithMailbox("");

    expect(container.querySelector("[data-testid=email-card-mailbox]")).toBeNull();
  });

  it("renders nothing when the host passes no label at all", () => {
    // Absent prop → pre-LUM-821 behaviour, so a host that does not know about
    // mailboxes is unchanged.
    renderCard(row({ source_label: "Work" }));

    expect(screen.queryByText("Work")).toBeNull();
  });
});

// --------------------------------------------------------------------------- //
// LUM-993 — Snooze is a menu of presets
// --------------------------------------------------------------------------- //
describe("EmailSignalCard — Snooze menu (LUM-993)", () => {
  it("lists the presets in a menu and snoozes with the chosen one", async () => {
    const { default: userEvent } = await import("@testing-library/user-event");
    const onSetTriage = vi.fn();
    renderCard(row(), { onSetTriage });
    await userEvent.click(screen.getByRole("button", { name: "Snooze" }));
    const menu = await screen.findByRole("menu");
    expect(menu).toBeInTheDocument();
    await userEvent.click(await screen.findByRole("menuitem", { name: "Tomorrow" }));
    expect(onSetTriage).toHaveBeenCalledWith("s1", "snoozed", expect.stringMatching(/^\d{4}-\d{2}-\d{2}T/));
  });

  it("offers no menu for a picked preset until it is opened", () => {
    renderCard(row());
    expect(screen.queryByRole("menu")).toBeNull();
  });
});
