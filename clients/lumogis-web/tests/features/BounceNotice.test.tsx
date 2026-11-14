// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// "This message did not arrive" (LUM-820).
//
// This is the half a user can see. Everything before it stopped Lumogis losing
// the evidence; only this stops someone believing a message arrived when it
// did not.

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { EmailBounce } from "../../src/api/emailBounces";
import { BounceNotice } from "../../src/features/email/BounceNotice";

const BOUNCE: EmailBounce = {
  id: "b1",
  recipient: "jhon@example.com",
  status: "5.1.1",
  subject: "Lunch?",
  seen_at: null,
  sent_at: null,
};

describe("BounceNotice (LUM-820)", () => {
  it("names the message and the recipient, not an identifier", async () => {
    render(
      <BounceNotice fetchBounces={async () => [BOUNCE]} acknowledge={vi.fn(async () => {})} />,
    );

    expect(await screen.findByText("Lunch?")).toBeTruthy();
    expect(screen.getByText("jhon@example.com")).toBeTruthy();
  });

  it("renders nothing at all when there are no bounces", async () => {
    const { container } = render(
      <BounceNotice fetchBounces={async () => []} acknowledge={vi.fn(async () => {})} />,
    );

    await new Promise((r) => setTimeout(r, 20));
    expect(container.querySelector(".lumogis-bounce-notice")).toBeNull();
  });

  it("still names the recipient when the send row has been pruned", async () => {
    render(
      <BounceNotice
        fetchBounces={async () => [{ ...BOUNCE, subject: null }]}
        acknowledge={vi.fn(async () => {})}
      />,
    );

    expect(await screen.findByText("jhon@example.com")).toBeTruthy();
  });

  it("can be dismissed, so the notice cannot become wallpaper", async () => {
    const acknowledge = vi.fn(async () => {});
    render(<BounceNotice fetchBounces={async () => [BOUNCE]} acknowledge={acknowledge} />);
    await screen.findByText("Lunch?");

    await userEvent.click(screen.getByRole("button", { name: /Dismiss notice/ }));

    await waitFor(() => expect(screen.queryByText("Lunch?")).toBeNull());
    expect(acknowledge).toHaveBeenCalledWith("b1");
  });

  it("a failed acknowledge leaves it to return on the next load", async () => {
    // The right direction to be wrong in: a notice that looks dismissed but was
    // never recorded is worse than one that reappears.
    const acknowledge = vi.fn(async () => Promise.reject(new Error("500")));
    render(<BounceNotice fetchBounces={async () => [BOUNCE]} acknowledge={acknowledge} />);
    await screen.findByText("Lunch?");

    await userEvent.click(screen.getByRole("button", { name: /Dismiss notice/ }));

    await waitFor(() => expect(acknowledge).toHaveBeenCalled());
  });

  it("a failed lookup shows no error banner over the inbox", async () => {
    const { container } = render(
      <BounceNotice
        fetchBounces={async () => Promise.reject(new Error("500"))}
        acknowledge={vi.fn(async () => {})}
      />,
    );

    await new Promise((r) => setTimeout(r, 20));
    expect(container.querySelector(".lumogis-bounce-notice")).toBeNull();
  });

  it("is announced as an alert, not left for the user to notice", async () => {
    render(
      <BounceNotice fetchBounces={async () => [BOUNCE]} acknowledge={vi.fn(async () => {})} />,
    );

    expect(await screen.findByRole("alert")).toBeTruthy();
  });

  it("pluralises so the heading reads correctly for several", async () => {
    render(
      <BounceNotice
        fetchBounces={async () => [BOUNCE, { ...BOUNCE, id: "b2", recipient: "x@y.example" }]}
        acknowledge={vi.fn(async () => {})}
      />,
    );

    expect(await screen.findByText("2 messages you sent did not arrive")).toBeTruthy();
  });
});
