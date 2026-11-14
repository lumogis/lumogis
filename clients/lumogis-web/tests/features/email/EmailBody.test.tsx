// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
// LUM-810 — the read view's presentation.
//
// Two things this file pins that the ADR insists on: the text-only limitation
// is *stated* rather than discovered, and each failure gets its own sentence
// instead of a generic "couldn't load".
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { EmailBody } from "../../../src/features/email/EmailBody";
import { EmailSignalCard } from "../../../src/features/email/EmailSignalCard";
import type { EmailSignalRow } from "../../../src/api/emailSources";

describe("EmailBody", () => {
  it("renders the message text", () => {
    render(<EmailBody body="The quick brown fox." />);
    expect(screen.getByText(/quick brown fox/)).toBeTruthy();
  });

  it("always states the text-only limitation", () => {
    render(<EmailBody body="hi" />);
    expect(screen.getByText(/Showing text only/)).toBeTruthy();
  });

  it("collapses a long body and expands on request", () => {
    render(<EmailBody body={"x".repeat(500)} />);

    const toggle = screen.getByRole("button", { name: "Show all" });
    expect(screen.getByText(/…$/)).toBeTruthy();

    fireEvent.click(toggle);

    expect(screen.getByRole("button", { name: "Show less" })).toBeTruthy();
  });

  it("does not offer expansion for a short body", () => {
    render(<EmailBody body="short" />);
    expect(screen.queryByRole("button", { name: "Show all" })).toBeNull();
  });

  it("says so when the message was truncated", () => {
    render(<EmailBody body="partial" truncated />);
    expect(screen.getByText(/Message truncated/)).toBeTruthy();
  });

  it("shows a loading state rather than an empty body", () => {
    render(<EmailBody body="" loading />);
    expect(screen.getByRole("status")).toBeTruthy();
  });

  it.each([
    ["mailbox_unreachable", /Mailbox unreachable/],
    ["no_body", /No message text available/],
    ["not_found", /no longer in your mailbox/],
    ["too_large", /too large to show here/],
    ["ambiguous", /identify this message uniquely/],
  ] as const)("explains %s in its own words", (reason, copy) => {
    render(<EmailBody body="" reason={reason} />);
    expect(screen.getByText(copy)).toBeTruthy();
  });

  it("offers retry only for the retryable failure", () => {
    const onRetry = vi.fn();
    const { rerender } = render(
      <EmailBody body="" reason="mailbox_unreachable" degraded onRetry={onRetry} />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(onRetry).toHaveBeenCalledOnce();

    // `not_found` is terminal — offering a retry that can never work is the
    // affordance-that-cannot-succeed bug LUM-719 was filed for.
    rerender(<EmailBody body="" reason="not_found" onRetry={onRetry} />);
    expect(screen.queryByRole("button", { name: "Try again" })).toBeNull();
  });
});

// --- the card's read affordance (LUM-810 + LUM-719) -------------------------

describe("EmailSignalCard read affordance", () => {
  const row = (o: Partial<EmailSignalRow> = {}): EmailSignalRow => ({
    signal_id: "s1",
    source_id: "src",
    title: "Dentist appointment",
    url: "imap://src/1/1",
    category: "health",
    category_label: "Health",
    scope: "personal",
    published_at: "2026-07-20T09:00:00Z",
    content_summary: "Confirm your Thursday 3pm appointment.",
    ...o,
  });

  const client = {} as never;
  const noop = () => {};
  const handlers = { onSetTriage: noop, onClearTriage: noop, onFeedback: noop };

  it("offers the Message disclosure to an owner", () => {
    render(<EmailSignalCard signal={row()} {...handlers} bodyClient={client} readable />);
    expect(screen.getByTestId("body-s1")).toBeTruthy();
  });

  it("owner still sees it when the representative row is the shared projection", () => {
    // `collapseProjections` hands the owner their own mail's shared projection
    // (LUM-661). Gating on raw `scope` would hide the button from the person
    // whose mailbox it is — a bug the shared-scope test alone would not catch.
    render(
      <EmailSignalCard
        signal={row({ scope: "shared", published_from: "original-1" })}
        {...handlers}
        bodyClient={client}
        readable
      />,
    );
    expect(screen.getByTestId("body-s1")).toBeTruthy();
  });

  it("explains instead of offering when the mailbox is not the viewer's", () => {
    render(
      <EmailSignalCard
        signal={row({ scope: "shared", from_display: "Sarah" })}
        {...handlers}
        bodyClient={client}
        readable={false}
      />,
    );
    expect(screen.queryByTestId("body-s1")).toBeNull();
    expect(screen.getByText(/only they can open this message/)).toBeTruthy();
  });

  it("reads as loading before the body is fetched, not as an empty message (LUM-860)", () => {
    // The disclosure's content is in the DOM while closed and shows in the
    // same frame it opens, before the fetch starts. It must not claim
    // "Showing text only" over a body that has not arrived.
    render(<EmailSignalCard signal={row()} {...handlers} bodyClient={client} readable />);
    const disclosure = screen.getByTestId("body-s1");
    expect(disclosure.textContent).toContain("Loading message…");
    expect(disclosure.textContent).not.toMatch(/Showing text only/);
  });

  it("is absent entirely when no body client is supplied", () => {
    render(<EmailSignalCard signal={row()} {...handlers} />);
    expect(screen.queryByTestId("body-s1")).toBeNull();
    expect(screen.queryByText(/only they can open this message/)).toBeNull();
  });
});

// --- HTTP failures are not all "mailbox unreachable" ------------------------

describe("EmailBody failure copy for client-synthesised reasons", () => {
  it("a rate limit says wait, and offers no retry that would trip it again", () => {
    const onRetry = vi.fn();
    render(<EmailBody body="" reason="rate_limited" onRetry={onRetry} />);

    expect(screen.getByText(/wait a minute/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Try again" })).toBeNull();
  });

  it("an unavailable message uses the same copy as a genuine miss", () => {
    // The route 404s for a signal that is not the caller's own, with the same
    // shape as a real miss. The client must not turn that into an oracle.
    render(<EmailBody body="" reason="unavailable" />);

    expect(screen.getByText(/Can’t open this message/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Try again" })).toBeNull();
  });
});
