// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-737 — e2e for the LUM-705 reply-all surface.
//
// Hybrid: smoke login + page.route mocks, same pattern as email_send.spec.ts.
// No live mailbox is needed — only a running web app and a logged-in session.
//
// SCOPE, AND WHAT IS DELIBERATELY NOT HERE. The recipient algebra — own address
// removed, sender not duplicated into Cc, duplicates across To/Cc collapsed,
// display names containing commas, folded headers, the truncation cap — is
// computed server-side and returned ready-made as `cc[]` by
// POST /api/v1/email/reply-all. orchestrator/tests/test_email_reply_all.py
// covers every one of those as a unit. Re-asserting them here would mean
// hand-writing the answer into a mock and then checking the mock still says it:
// a test that passes when the product is broken. So this file pins only what a
// browser can actually falsify — that the resolved recipients reach the
// composer, and that each degrade renders its own distinct copy.
//
// THE ONE THAT MATTERS. `EmailComposer` opens the Cc/Bcc disclosure when
// `initialCc` is non-empty, and the source comment says why: "A pre-filled Cc
// hidden behind a collapsed disclosure is the version of this feature that
// mails four people by surprise." That is a safety property about what the user
// can SEE before sending, so it is only provable in a rendered browser.
//
// NOTE ON ROUTE GLOBS: `/sources` and `/signals` are mounted WITHOUT the
// /api/v1 prefix, while /api/v1/email/* is prefixed. Matching
// `**/api/v1/sources**` silently never fires and every test here dies waiting
// for a card. That cost email_send.spec.ts four tests.

import { test, expect, type Page } from "@playwright/test";

import {
  hasSmokeCreds,
  loginWithSmokeCredentials,
  smokeCredsSkipMessage,
} from "./smoke-auth";

const SOURCE_ID = "11111111-1111-1111-1111-111111111111";
const SIGNAL_ID = "sig-e2e-replyall-1";
const SENDER = "bob@example.com";

interface ReplyAllOpts {
  cc?: string[];
  degraded?: boolean;
  truncated?: number;
  largeThread?: boolean;
}

function installMocks(page: Page, opts: ReplyAllOpts = {}): void {
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
            title: "Sports day logistics",
            from_addr: SENDER,
            from_display: "Bob",
            content_summary: "Who is bringing the gazebo?",
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

  // The server has already done the resolution; the UI only renders the result.
  page.route("**/api/v1/email/reply-all", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        to: [SENDER],
        cc: opts.cc ?? [],
        degraded: opts.degraded ?? false,
        dropped_self: false,
        truncated: opts.truncated ?? 0,
        large_thread: opts.largeThread ?? false,
      }),
    });
  });

  // Plain Reply pulls a suggested draft; keep it deterministic.
  page.route("**/api/v1/email/reply-draft**", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ draft: "Sure — I can bring it." }),
    });
  });
}

async function openCard(page: Page) {
  await page.goto("/email");
  const card = page.getByTestId(`email-card-${SIGNAL_ID}`);
  await expect(card).toBeVisible();
  return card;
}

test.describe("email reply-all (LUM-737)", () => {
  test.skip(!hasSmokeCreds, smokeCredsSkipMessage);

  test.beforeEach(async ({ page }) => {
    await loginWithSmokeCredentials(page);
  });

  test("resolved recipients reach the composer, and the Cc disclosure is already open", async ({
    page,
  }) => {
    installMocks(page, { cc: ["carol@example.com", "dave@example.com"] });
    const card = await openCard(page);

    await card.getByRole("button", { name: "Reply all" }).click();

    // Reply mode renders no To input — the recipient is fixed and shown in the
    // header instead, so the sender is asserted there rather than as a field.
    await expect(page.getByText(`In reply to: ${SENDER}`)).toBeVisible();
    await expect(page.getByLabel("Cc", { exact: true })).toHaveValue(
      "carol@example.com, dave@example.com",
    );

    // Open, not merely present: a pre-filled Cc behind a collapsed disclosure is
    // how this feature mails people the user never saw listed.
    await expect(page.getByTestId("cc-bcc-disclosure")).toHaveAttribute("open", "");
  });

  test("plain Reply and Reply all produce different recipient sets from one message", async ({
    page,
  }) => {
    // Guards the collapse directly: if Reply ever started pre-filling Cc too, or
    // Reply all stopped, every single-mode assertion above would still pass.
    installMocks(page, { cc: ["carol@example.com"] });
    const card = await openCard(page);

    await card.getByRole("button", { name: "Reply", exact: true }).click();
    await expect(page.getByLabel("Cc", { exact: true })).toHaveValue("");
    await expect(page.getByTestId("cc-bcc-disclosure")).not.toHaveAttribute("open", "");
  });

  test("an unreadable original degrades to a plain reply and says so", async ({ page }) => {
    // degraded always ships an empty cc, so the risk is silence: the user thinks
    // they replied to everyone. The notice is the whole feature here.
    installMocks(page, { cc: [], degraded: true });
    const card = await openCard(page);

    await card.getByRole("button", { name: "Reply all" }).click();

    await expect(
      page.getByText(/couldn.t read the other recipients — replying to the sender only/i),
    ).toBeVisible();
    await expect(page.getByLabel("Cc", { exact: true })).toHaveValue("");
  });

  test("recipients dropped by the cap are reported, not silently discarded", async ({ page }) => {
    installMocks(page, { cc: ["carol@example.com"], truncated: 12 });
    const card = await openCard(page);

    await card.getByRole("button", { name: "Reply all" }).click();

    await expect(page.getByText(/12 further recipients were left out/i)).toBeVisible();
  });

  test("a large thread warns before sending", async ({ page }) => {
    const cc = Array.from({ length: 9 }, (_, i) => `p${i}@example.com`);
    installMocks(page, { cc, largeThread: true });
    const card = await openCard(page);

    await card.getByRole("button", { name: "Reply all" }).click();

    await expect(page.getByText(/this reply goes to 9 people/i)).toBeVisible();
  });
});
