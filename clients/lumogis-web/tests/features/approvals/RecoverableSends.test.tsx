// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-793 — the failed-send recovery surface.
//
// The load-bearing assertions here are about what the row is allowed to SAY and
// what it is allowed to OFFER:
//
//   * a `sent` row offers no Save-to-Drafts — there is nothing to recover
//   * a `partially_sent` row DOES, and names who did and did not receive it.
//     Collapsing that into `sent` locks recovery shut for exactly the people
//     who never got the message, which is the worst thing this feature can do
//   * attachment counts come from the server's staging read, so the row cannot
//     promise files the sweep has already taken
//
// Reachability (the component having a production importer at all) is asserted
// separately by `tests/features/email/reachability.test.ts`, which LUM-793
// extended to `features/approvals/`.

import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ApiClient } from "../../../src/api/client";
import { AccessTokenStore } from "../../../src/api/tokens";
import { RecoverableSends } from "../../../src/features/approvals/RecoverableSends";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const BASE_ITEM = {
  proposal_id: 7,
  action_name: "email_compose",
  to: ["a@example.com"],
  subject: "Dinner Saturday",
  body: "Shall we say 7pm at the usual place?",
  failed_at: "2026-08-03T18:00:00Z",
  error: "smtp_send_unavailable: connection reset",
  outcome: "not_sent",
  sent_at: null,
  delivered_to: [],
  refused: [],
  attachments: { count: 0, available: false, expires_at: null },
};

interface Harness {
  client: ApiClient;
  calls: string[];
  saveBody: () => unknown;
}

function buildClient(
  items: object[],
  opts: { saveResponse?: () => Response; dismissStatus?: number } = {},
): Harness {
  const calls: string[] = [];
  let saveBody: unknown = null;

  const fetchImpl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    calls.push(`${init?.method ?? "GET"} ${url}`);

    if (url.includes("/auth/refresh")) {
      return jsonResponse(200, {
        access_token: "tok-test",
        token_type: "bearer",
        expires_in: 900,
        user: { id: "u1", email: "alice@home.lan", role: "user" },
      });
    }
    if (url.includes("/save-draft")) {
      saveBody = init?.body ?? null;
      return opts.saveResponse
        ? opts.saveResponse()
        : jsonResponse(200, {
            proposal_id: 7,
            success: true,
            error: null,
            code: null,
            attachments_dropped: 0,
          });
    }
    if (url.includes("/dismiss")) {
      return jsonResponse(opts.dismissStatus ?? 200, { proposal_id: 7, status: "archived" });
    }
    if (url.includes("/recoverable-sends")) {
      return jsonResponse(200, { items });
    }
    return jsonResponse(404, { detail: "unexpected" });
  });

  const tokens = new AccessTokenStore();
  const client = new ApiClient({
    tokens,
    fetchImpl: fetchImpl as unknown as typeof fetch,
  });
  return { client, calls, saveBody: () => saveBody };
}

afterEach(() => {
  vi.restoreAllMocks();
});

// --------------------------------------------------------------------------- //
// Rendering
// --------------------------------------------------------------------------- //

describe("RecoverableSends", () => {
  it("renders nothing when there is nothing to recover", async () => {
    const { client } = buildClient([]);
    const { container } = render(<RecoverableSends client={client} />);
    await waitFor(() => expect(container.querySelector("section")).toBeNull());
  });

  it("shows the message body, subject and recipients", async () => {
    const { client } = buildClient([BASE_ITEM]);
    render(<RecoverableSends client={client} />);

    // Seeing the text IS the reassurance — a summary would defeat the point.
    expect(await screen.findByTestId("recovery-body")).toHaveTextContent(
      "Shall we say 7pm at the usual place?",
    );
    expect(screen.getByText("Dinner Saturday")).toBeInTheDocument();
    expect(screen.getByText(/a@example\.com/)).toBeInTheDocument();
  });

  it("states a not-sent outcome plainly", async () => {
    const { client } = buildClient([BASE_ITEM]);
    render(<RecoverableSends client={client} />);
    expect(await screen.findByText(/Not sent/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save to Drafts" })).toBeInTheDocument();
  });

  it("renders a sent row with NO Save-to-Drafts button", async () => {
    const { client } = buildClient([
      { ...BASE_ITEM, outcome: "sent", sent_at: "2026-08-03T18:00:05Z", delivered_to: ["a@example.com"] },
    ]);
    render(<RecoverableSends client={client} />);

    expect(await screen.findByText(/Sent/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Save to Drafts" })).toBeNull();
    // Dismiss stays — the user still needs a way to clear the row.
    expect(screen.getByRole("button", { name: "Dismiss" })).toBeInTheDocument();
  });

  it("offers recovery for a PARTIAL send and names both sides", async () => {
    const { client } = buildClient([
      {
        ...BASE_ITEM,
        to: ["a@example.com", "b@example.com"],
        outcome: "partially_sent",
        sent_at: "2026-08-03T18:00:05Z",
        delivered_to: ["a@example.com"],
        refused: ["b@example.com"],
      },
    ]);
    render(<RecoverableSends client={client} />);

    expect(await screen.findByText(/Partly sent/)).toBeInTheDocument();
    expect(screen.getByText(/a@example\.com received it/)).toBeInTheDocument();
    expect(screen.getByText(/b@example\.com did not/)).toBeInTheDocument();
    // The whole point: b@example.com never got it, so recovery must stay open.
    expect(screen.getByRole("button", { name: "Save to Drafts" })).toBeInTheDocument();
  });

  it("says it couldn't confirm rather than hedging about what happened", async () => {
    const { client } = buildClient([{ ...BASE_ITEM, outcome: "unknown" }]);
    render(<RecoverableSends client={client} />);
    expect(await screen.findByText(/Couldn’t confirm/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save to Drafts" })).toBeInTheDocument();
  });

  it("reports attachment availability with a DATED expiry", async () => {
    // Dated, not just a weekday. The grace is exactly 7 days, so `expires_at`
    // always lands on the same weekday as `failed_at` — a weekday-only expiry
    // rendered as "failed Mon 6:00 PM … available until Mon 6:00 PM", which
    // reads as a window that has already closed.
    const { client } = buildClient([
      {
        ...BASE_ITEM,
        failed_at: "2026-08-03T18:00:00Z", // a Monday
        attachments: { count: 2, available: true, expires_at: "2026-08-10T18:00:00Z" }, // +7d
      },
    ]);
    render(<RecoverableSends client={client} />);
    const el = await screen.findByTestId("recovery-attachments");
    expect(el).toHaveTextContent("2 files");
    expect(el).toHaveTextContent(/available until/);
    // The day-of-month is what disambiguates the two Mondays.
    expect(el).toHaveTextContent(/10/);
    expect(el.textContent).not.toMatch(/available until \w+ \d+:\d+/);
  });

  it("dates the failure time too, since a row can be 30 days old", async () => {
    const { client } = buildClient([{ ...BASE_ITEM, failed_at: "2026-07-06T18:00:00Z" }]);
    render(<RecoverableSends client={client} />);
    const row = await screen.findByTestId("recoverable-send");
    // "failed Mon" is ambiguous across four Mondays inside the listing window.
    expect(row.textContent).toMatch(/failed[^]*6/);
  });

  it("collapses a long body behind Show all", async () => {
    const user = userEvent.setup();
    const long = "x".repeat(500);
    const { client } = buildClient([{ ...BASE_ITEM, body: long }]);
    render(<RecoverableSends client={client} />);

    const body = await screen.findByTestId("recovery-body");
    expect(body.textContent!.length).toBeLessThan(long.length);
    await user.click(screen.getByRole("button", { name: "Show all" }));
    expect(await screen.findByTestId("recovery-body")).toHaveTextContent(long);
  });
});

// --------------------------------------------------------------------------- //
// Actions
// --------------------------------------------------------------------------- //

describe("RecoverableSends actions", () => {
  it("retires the Save button after a successful save", async () => {
    // The row stays listed on purpose — dismissing is the user's call. But a
    // second click would APPEND a second identical draft to the real mailbox,
    // and `busy` only covers the in-flight request.
    const user = userEvent.setup();
    const { client, calls } = buildClient([BASE_ITEM]);
    render(<RecoverableSends client={client} />);

    await user.click(await screen.findByRole("button", { name: "Save to Drafts" }));
    await screen.findByText("Saved to Drafts.");

    expect(screen.queryByRole("button", { name: "Save to Drafts" })).toBeNull();
    expect(screen.getByTestId("recoverable-send")).toBeInTheDocument();
    expect(calls.filter((c) => c.includes("/save-draft")).length).toBe(1);
  });

  it("keeps the Save button after a FAILED save, so it can be retried", async () => {
    const user = userEvent.setup();
    const { client } = buildClient([BASE_ITEM], {
      saveResponse: () =>
        jsonResponse(200, {
          proposal_id: 7,
          success: false,
          error: "mailbox unreachable",
          code: null,
          attachments_dropped: 0,
        }),
    });
    render(<RecoverableSends client={client} />);

    await user.click(await screen.findByRole("button", { name: "Save to Drafts" }));
    await screen.findByText(/mailbox unreachable/);
    expect(screen.getByRole("button", { name: "Save to Drafts" })).toBeInTheDocument();
  });

  it("saves to Drafts and confirms", async () => {
    const user = userEvent.setup();
    const { client, calls } = buildClient([BASE_ITEM]);
    render(<RecoverableSends client={client} />);

    await user.click(await screen.findByRole("button", { name: "Save to Drafts" }));
    expect(await screen.findByText("Saved to Drafts.")).toBeInTheDocument();
    expect(
      calls.some((c) => c.startsWith("POST") && c.includes("/recoverable-sends/7/save-draft")),
    ).toBe(true);
  });

  it("says which attachments were dropped rather than refusing the save", async () => {
    const user = userEvent.setup();
    const { client } = buildClient([BASE_ITEM], {
      saveResponse: () =>
        jsonResponse(200, {
          proposal_id: 7,
          success: true,
          error: null,
          code: null,
          attachments_dropped: 2,
        }),
    });
    render(<RecoverableSends client={client} />);

    await user.click(await screen.findByRole("button", { name: "Save to Drafts" }));
    // A draft is recoverable by definition — refusing to save it would leave
    // the user with nothing, which is strictly worse than an honest sentence.
    expect(await screen.findByText(/2 files were no longer available/)).toBeInTheDocument();
    expect(screen.getByText(/re-attach/)).toBeInTheDocument();
  });

  it("names the no-Drafts-folder case instead of claiming success", async () => {
    const user = userEvent.setup();
    const { client } = buildClient([BASE_ITEM], {
      saveResponse: () =>
        jsonResponse(200, {
          proposal_id: 7,
          success: false,
          error: "mailbox has no Drafts folder",
          code: "no_drafts_folder",
          attachments_dropped: 0,
        }),
    });
    render(<RecoverableSends client={client} />);

    await user.click(await screen.findByRole("button", { name: "Save to Drafts" }));
    expect(await screen.findByText(/no Drafts folder/)).toBeInTheDocument();
    // The row must remain — being able to try again is the entire feature.
    expect(screen.getByTestId("recoverable-send")).toBeInTheDocument();
  });

  it("explains the server's 409 when the row turned out to be sent", async () => {
    const user = userEvent.setup();
    const { client } = buildClient([BASE_ITEM], {
      saveResponse: () => jsonResponse(409, { detail: { error: "already_sent" } }),
    });
    render(<RecoverableSends client={client} />);

    await user.click(await screen.findByRole("button", { name: "Save to Drafts" }));
    expect(await screen.findByText(/actually sent/)).toBeInTheDocument();
  });

  it("keeps the row and calls nothing when the dismiss confirm is cancelled", async () => {
    const user = userEvent.setup();
    const { client, calls } = buildClient([BASE_ITEM]);
    render(<RecoverableSends client={client} />);

    await user.click(await screen.findByRole("button", { name: "Dismiss" }));
    await user.click(
      within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Keep it" }),
    );

    expect(screen.getByTestId("recoverable-send")).toBeInTheDocument();
    expect(calls.some((c) => c.includes("/dismiss"))).toBe(false);
  });

  it("removes the row on dismiss", async () => {
    const user = userEvent.setup();
    const { client, calls } = buildClient([BASE_ITEM]);
    render(<RecoverableSends client={client} />);

    await user.click(await screen.findByRole("button", { name: "Dismiss" }));
    // Dismissing throws the recoverable text away, so it is confirmed first.
    await user.click(
      within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Dismiss" }),
    );
    await waitFor(() => expect(screen.queryByTestId("recoverable-send")).toBeNull());
    expect(
      calls.some((c) => c.startsWith("POST") && c.includes("/recoverable-sends/7/dismiss")),
    ).toBe(true);
  });

  it("keeps the row when dismiss fails", async () => {
    const user = userEvent.setup();
    const { client } = buildClient([BASE_ITEM], { dismissStatus: 500 });
    render(<RecoverableSends client={client} />);

    await user.click(await screen.findByRole("button", { name: "Dismiss" }));
    // Dismissing throws the recoverable text away, so it is confirmed first.
    await user.click(
      within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Dismiss" }),
    );
    expect(await screen.findByText(/still here/)).toBeInTheDocument();
    expect(screen.getByTestId("recoverable-send")).toBeInTheDocument();
  });
});
