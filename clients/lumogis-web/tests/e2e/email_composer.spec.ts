// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-706 — e2e for the LUM-698 composer surface: forward, the Cc/Bcc
// disclosure, and trimming the quoted original before it is sent.
//
// Hybrid: smoke login + page.route mocks, same pattern as email_send.spec.ts.
//
// WHY THESE FOUR AND NOT THE OTHER FIFTEEN. LUM-698 shipped with 15 Vitest
// cases on EmailComposer. The behaviours re-tested here are the ones jsdom can
// only approximate:
//
//   * Native <details> toggle semantics. The reply-mode quote fetch is LAZY,
//     gated on the first real `ontoggle`. jsdom's handling is not the browser's,
//     so "does opening the disclosure trigger the fetch, and does leaving it
//     closed avoid one" is a browser question.
//   * The edit-then-send round trip. A unit test can assert the payload builder;
//     only an e2e proves the trimmed text survives form → confirm card →
//     execute and is what actually leaves.
//   * Omission. Empty cc/bcc are deliberately OMITTED from the payload rather
//     than sent as empty arrays, so that an unused feature changes no existing
//     request byte-for-byte. Asserting a key is ABSENT is the whole point.
//
// NOTE ON ROUTE GLOBS: `/sources` and `/signals` are bare; /api/v1/email/* and
// /api/v1/approvals/* are prefixed. Getting that wrong hides the Email nav
// entirely and every test here waits 120s for a card.

import { test, expect, type Page } from "@playwright/test";

import {
  hasSmokeCreds,
  loginWithSmokeCredentials,
  smokeCredsSkipMessage,
} from "./smoke-auth";

const SOURCE_ID = "11111111-1111-1111-1111-111111111111";
const SIGNAL_ID = "sig-e2e-composer-1";
const SUBJECT = "Roof quote";
const QUOTE = "> On Friday, Bob wrote:\n> The quote is 2,400.";

interface Created {
  action_name: string;
  payload: Record<string, unknown>;
}

interface Recorder {
  created: () => Created[];
  quoteCalls: () => string[];
}

function installMocks(page: Page): Recorder {
  const created: Created[] = [];
  const quoteCalls: string[] = [];
  let nextId = 200;

  page.route("**/sources**", async (route) => {
    if (route.request().method() !== "GET") return route.continue();
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        sources: [
          {
            id: SOURCE_ID,
            name: "Email (personal)",
            source_type: "imap",
            url: "imap://imap.example.com:993/personal",
            category: "email",
            active: true,
            poll_interval: 900,
            last_polled_at: "2026-07-28T00:00:00Z",
            last_signal_at: null,
            polling_active: true,
          },
        ],
        total: 1,
      }),
    });
  });

  page.route("**/signals**", async (route) => {
    if (route.request().method() !== "GET") return route.continue();
    const triaged = route.request().url().includes("triaged=set");
    const signals = triaged
      ? []
      : [
          {
            signal_id: SIGNAL_ID,
            source_id: SOURCE_ID,
            title: SUBJECT,
            from_addr: "bob@example.com",
            from_display: "Bob",
            content_summary: "The quote is 2,400.",
            category: "email",
            category_label: "Email",
            importance_score: 0.9,
            created_at: "2026-07-28T09:00:00Z",
            published_at: "2026-07-28T09:00:00Z",
            triage_status: null,
          },
        ];
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ signals, total: signals.length, next_before: null }),
    });
  });

  // Every hit is recorded so "was it fetched at all, and when" is assertable.
  page.route("**/api/v1/email/quote**", async (route) => {
    const body = route.request().postDataJSON() as { style?: string } | null;
    quoteCalls.push(body?.style ?? "unknown");
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ quote: QUOTE }),
    });
  });

  page.route("**/api/v1/email/reply-draft**", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ draft: "" }),
    });
  });

  page.route("**/api/v1/approvals/proposals", async (route) => {
    if (route.request().method() !== "POST") return route.continue();
    const body = JSON.parse(route.request().postData() || "{}");
    created.push({ action_name: body.action_name, payload: body.payload ?? {} });
    await route.fulfill({
      status: 201,
      contentType: "application/json",
      body: JSON.stringify({ proposal_id: nextId++, status: "pending", first_contact: false }),
    });
  });

  page.route(/\/api\/v1\/approvals\/proposals\/\d+\/approve$/, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ status: "approved" }),
    });
  });

  page.route(/\/api\/v1\/approvals\/proposals\/\d+\/execute$/, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        success: true,
        output: "ok",
        error: null,
        reverse_token: null,
        code: null,
      }),
    });
  });

  return { created: () => created, quoteCalls: () => quoteCalls };
}

async function openCard(page: Page) {
  await page.goto("/email");
  const card = page.getByTestId(`email-card-${SIGNAL_ID}`);
  await expect(card).toBeVisible();
  return card;
}

test.describe("email composer (LUM-706)", () => {
  test.skip(!hasSmokeCreds, smokeCredsSkipMessage);

  test.beforeEach(async ({ page }) => {
    await loginWithSmokeCredentials(page);
  });

  test("Forward seeds a Fwd: subject and opens the quote disclosure by default", async ({
    page,
  }) => {
    const rec = installMocks(page);
    const card = await openCard(page);

    await card.getByRole("button", { name: "Forward" }).click();
    const dialog = page.getByRole("dialog", { name: "Forward email" });
    await expect(dialog).toBeVisible();

    await expect(dialog.getByLabel("Subject")).toHaveValue(`Fwd: ${SUBJECT}`);
    // In forward mode the quoted content IS the message, so it is open and
    // fetched eagerly — the opposite of reply mode below.
    await expect(dialog.getByTestId("quote-disclosure")).toHaveAttribute("open", "");
    await expect(dialog.getByLabel("Quoted original")).toHaveValue(QUOTE);
    await expect.poll(() => rec.quoteCalls()).toEqual(["forward"]);
  });

  test("reply does not fetch the quote until the disclosure is expanded", async ({ page }) => {
    const rec = installMocks(page);
    const card = await openCard(page);

    await card.getByRole("button", { name: "Reply", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "Reply to email" });
    await expect(dialog.getByLabel("Message")).toBeVisible();

    // Collapsed: an ordinary reply must not pay for a second IMAP round-trip.
    await expect(dialog.getByTestId("quote-disclosure")).not.toHaveAttribute("open", "");
    expect(rec.quoteCalls()).toEqual([]);

    await dialog.getByText("Quoted original").click();
    await expect.poll(() => rec.quoteCalls()).toEqual(["reply"]);
    await expect(dialog.getByLabel("Quoted original")).toHaveValue(QUOTE);
  });

  test("a trimmed quote is what actually gets sent", async ({ page }) => {
    const rec = installMocks(page);
    const card = await openCard(page);

    await card.getByRole("button", { name: "Reply", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "Reply to email" });

    await dialog.getByLabel("Message").fill("Confirming 2,400.");
    await dialog.getByText("Quoted original").click();
    await expect(dialog.getByLabel("Quoted original")).toHaveValue(QUOTE);

    // The user cuts the thread down before sending. The edited text — not the
    // fetched original — is what must leave.
    await dialog.getByLabel("Quoted original").fill("> The quote is 2,400.");

    await dialog.getByRole("button", { name: "Review" }).click();
    await dialog.getByRole("button", { name: "Send" }).click();
    await expect(dialog.getByText("Sent ✓")).toBeVisible();

    const reply = rec.created().find((c) => c.action_name === "email_reply");
    expect(reply?.payload.body).toBe("Confirming 2,400.\n\n> The quote is 2,400.");
    expect(reply?.payload.body).not.toContain("On Friday, Bob wrote");
  });

  test("Cc and Bcc reach the payload when filled", async ({ page }) => {
    const rec = installMocks(page);
    const card = await openCard(page);

    await card.getByRole("button", { name: "Reply", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "Reply to email" });

    await dialog.getByLabel("Message").fill("Noted.");
    await dialog.getByText("Cc / Bcc").click();
    await dialog.getByLabel("Cc", { exact: true }).fill("carol@example.com");
    await dialog.getByLabel("Bcc", { exact: true }).fill("dave@example.com");

    await dialog.getByRole("button", { name: "Review" }).click();
    await dialog.getByRole("button", { name: "Send" }).click();
    await expect(dialog.getByText("Sent ✓")).toBeVisible();

    const reply = rec.created().find((c) => c.action_name === "email_reply");
    expect(reply?.payload.cc).toEqual(["carol@example.com"]);
    expect(reply?.payload.bcc).toEqual(["dave@example.com"]);
  });

  test("empty Cc and Bcc are omitted from the payload, not sent as empty lists", async ({
    page,
  }) => {
    // Absence, not emptiness. A composer that adds `cc: []` changes every
    // existing request shape, which is exactly what LUM-698 avoided — and no
    // rendered assertion can tell the two apart.
    const rec = installMocks(page);
    const card = await openCard(page);

    await card.getByRole("button", { name: "Reply", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "Reply to email" });

    await dialog.getByLabel("Message").fill("Noted.");
    await dialog.getByRole("button", { name: "Review" }).click();
    await dialog.getByRole("button", { name: "Send" }).click();
    await expect(dialog.getByText("Sent ✓")).toBeVisible();

    const reply = rec.created().find((c) => c.action_name === "email_reply");
    expect(reply).toBeTruthy();
    expect(Object.keys(reply!.payload)).not.toContain("cc");
    expect(Object.keys(reply!.payload)).not.toContain("bcc");
  });
});
