// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-656 — e2e for the LUM-642 /email surface: the nav gate, the three lanes,
// rich cards, and the search hand-off. Desktop + mobile viewports.
//
// Hybrid: smoke login + page.route mocks, same pattern as email_send.spec.ts.
//
// THE GATE IS THE POINT. `/email` is not always in the nav — it appears only
// once a mailbox is configured (App.tsx: `isEmailActivated(sources)`). A spec
// that only ever mocks a configured mailbox proves the item can appear, never
// that it is actually gated, so the no-mailbox case is asserted too. Without
// that negative, a regression making the item unconditional passes silently.
//
// NOTE ON ROUTE GLOBS: `/sources` and `/signals` are mounted WITHOUT the
// /api/v1 prefix. Matching `**/api/v1/sources**` never fires, the gate stays
// closed, and every test dies waiting for a lane.

import { test, expect, type Page } from "@playwright/test";

import {
  hasSmokeCreds,
  loginWithSmokeCredentials,
  smokeCredsSkipMessage,
} from "./smoke-auth";

const SOURCE_ID = "11111111-1111-1111-1111-111111111111";

const SIGNALS = [
  {
    signal_id: "sig-e2e-surface-1",
    title: "Sports day is Friday",
    from_addr: "office@school.example.com",
    from_display: "Fairview Primary",
    content_summary: "Gates open at 9.",
    triage_status: null,
  },
  {
    signal_id: "sig-e2e-surface-2",
    title: "Your statement is ready",
    from_addr: "no-reply@bank.example.com",
    from_display: "Example Bank",
    content_summary: "July statement available.",
    triage_status: "done",
  },
];

async function expectNoPageHorizontalOverflow(page: Page): Promise<void> {
  const { scrollW, clientW } = await page.evaluate(() => ({
    scrollW: document.documentElement.scrollWidth,
    clientW: document.documentElement.clientWidth,
  }));
  expect(
    scrollW,
    `scrollWidth ${scrollW} should not exceed clientWidth ${clientW} much`,
  ).toBeLessThanOrEqual(clientW + 1);
}

function installMocks(page: Page, opts: { mailbox?: boolean } = {}): void {
  const mailbox = opts.mailbox ?? true;

  page.route("**/sources**", async (route) => {
    if (route.request().method() !== "GET") return route.continue();
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        sources: mailbox
          ? [
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
            ]
          : [],
        total: mailbox ? 1 : 0,
      }),
    });
  });

  page.route("**/signals**", async (route) => {
    if (route.request().method() !== "GET") return route.continue();
    const triaged = route.request().url().includes("triaged=set");
    // The triaged=set fetch supplies the Done lane; the windowed fetch supplies
    // the untriaged ones. Splitting them mirrors the real two-fetch shape —
    // returning everything twice double-renders the cards.
    const rows = SIGNALS.filter((s) =>
      triaged ? s.triage_status !== null : s.triage_status === null,
    ).map((s) => ({
      signal_id: s.signal_id,
      source_id: SOURCE_ID,
      title: s.title,
      from_addr: s.from_addr,
      from_display: s.from_display,
      content_summary: s.content_summary,
      category: "email",
      category_label: "Email",
      importance_score: 0.9,
      created_at: "2026-07-28T09:00:00Z",
      published_at: "2026-07-28T09:00:00Z",
      triage_status: s.triage_status,
    }));
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ signals: rows, total: rows.length, next_before: null }),
    });
  });
}

test.describe("email surface (LUM-656)", () => {
  test.skip(!hasSmokeCreds, smokeCredsSkipMessage);

  test("the Email nav item appears once a mailbox is configured", async ({ page }) => {
    installMocks(page);
    await loginWithSmokeCredentials(page);

    await expect(page.getByRole("link", { name: "Email", exact: true })).toBeVisible();
  });

  test("with no mailbox configured there is no Email nav item", async ({ page }) => {
    // The negative half of the gate. Without this, an unconditional nav item
    // still passes every other test in this file.
    installMocks(page, { mailbox: false });
    await loginWithSmokeCredentials(page);

    await expect(page.getByTestId("lumogis-shell")).toBeVisible();
    await expect(page.getByRole("link", { name: "Email", exact: true })).toHaveCount(0);
  });

  test("/email renders all three lanes", async ({ page }) => {
    installMocks(page);
    await loginWithSmokeCredentials(page);
    await page.goto("/email");

    await expect(page.getByTestId("email-inbox-view")).toBeVisible();
    await expect(page.getByTestId("lane-needs_you")).toBeVisible();
    await expect(page.getByTestId("lane-good_to_know")).toBeVisible();
    await expect(page.getByTestId("lane-done")).toBeVisible();
  });

  test("a rich card carries sender and subject, and lands in the right lane", async ({ page }) => {
    installMocks(page);
    await loginWithSmokeCredentials(page);
    await page.goto("/email");

    // Scoped to the lane, not the page: asserting the card exists somewhere
    // would pass even if lane routing broke and everything piled into one.
    const needsYou = page.getByTestId("lane-needs_you");
    const card = needsYou.getByTestId("email-card-sig-e2e-surface-1");
    await expect(card).toBeVisible();
    await expect(card).toContainText("Sports day is Friday");
    await expect(card).toContainText("Fairview Primary");

    await expect(
      page.getByTestId("lane-done").getByTestId("email-card-sig-e2e-surface-2"),
    ).toBeVisible();
  });

  test("Search all email hands off to /search", async ({ page }) => {
    installMocks(page);
    await loginWithSmokeCredentials(page);
    await page.goto("/email");

    await page.getByRole("link", { name: /search all email/i }).click();
    await expect(page).toHaveURL(/\/search$/);
  });
});

test.describe("email surface on mobile (LUM-656)", () => {
  test.use({ viewport: { width: 390, height: 844 } });

  test.skip(!hasSmokeCreds, smokeCredsSkipMessage);

  test("/email renders lanes and cards without horizontal overflow", async ({ page }) => {
    installMocks(page);
    await loginWithSmokeCredentials(page);
    await page.goto("/email");

    await expect(page.getByTestId("email-inbox-view")).toBeVisible();
    await expect(page.getByTestId("lane-needs_you")).toBeVisible();
    await expect(page.getByTestId("email-card-sig-e2e-surface-1")).toBeVisible();

    // Long subjects and sender addresses are the usual cause of a mobile
    // sideways scroll, so the assertion runs with cards rendered, not on an
    // empty surface where it could not fail.
    await expectNoPageHorizontalOverflow(page);
  });
});
