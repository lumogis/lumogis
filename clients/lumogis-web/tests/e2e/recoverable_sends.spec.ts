// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-793 — failed-send recovery, end to end on the Approvals page.
//
// Hybrid: smoke login + `page.route` mocks (the email_send.spec.ts pattern), so
// no live mailbox and no seeded `dead` proposal are needed — the recovery
// listing is a plain GET, which is exactly the seam a route mock fits. Both
// viewports, because this is a user-visible surface and the actions row wraps
// on a phone.
//
// The assertions that matter are the ones a unit test cannot make: that the
// section is REACHABLE from the Approvals page a user actually navigates to,
// and that a fully-sent row shows up without a Save-to-Drafts button on a real
// render rather than in jsdom.

import { test, expect, type Page } from "@playwright/test";

import { hasSmokeCreds, loginWithSmokeCredentials, smokeCredsSkipMessage } from "./smoke-auth";

const NOT_SENT = {
  proposal_id: 501,
  action_name: "email_compose",
  to: ["bob@example.com"],
  subject: "Saturday dinner",
  body: "Shall we say 7pm at the usual place?",
  failed_at: "2026-08-03T18:00:00Z",
  error: "smtp_send_unavailable: connection reset",
  outcome: "not_sent",
  sent_at: null,
  delivered_to: [],
  refused: [],
  attachments: { count: 0, available: false, expires_at: null },
};

const PARTIAL = {
  ...NOT_SENT,
  proposal_id: 502,
  subject: "Street party",
  to: ["bob@example.com", "carol@example.com"],
  outcome: "partially_sent",
  sent_at: "2026-08-03T18:00:05Z",
  delivered_to: ["bob@example.com"],
  refused: ["carol@example.com"],
};

const SENT = {
  ...NOT_SENT,
  proposal_id: 503,
  subject: "Bin day",
  outcome: "sent",
  sent_at: "2026-08-03T18:00:05Z",
  delivered_to: ["bob@example.com"],
};

interface Recorder {
  saved: () => number[];
  dismissed: () => number[];
}

function installMocks(page: Page, items: object[]): Recorder {
  const saved: number[] = [];
  const dismissed: number[] = [];

  // The page's own data. Empty so the recovery section is the only content.
  page.route("**/api/v1/approvals/pending**", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ pending: [] }),
    }),
  );
  page.route("**/api/v1/approvals/proposals**", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ proposals: [] }),
    }),
  );

  page.route("**/api/v1/approvals/recoverable-sends**", async (route) => {
    const url = route.request().url();
    if (url.includes("/save-draft")) {
      saved.push(Number(url.match(/recoverable-sends\/(\d+)\//)?.[1] ?? 0));
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          proposal_id: saved[saved.length - 1],
          success: true,
          error: null,
          code: null,
          attachments_dropped: 0,
        }),
      });
    }
    if (url.includes("/dismiss")) {
      dismissed.push(Number(url.match(/recoverable-sends\/(\d+)\//)?.[1] ?? 0));
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ proposal_id: dismissed[dismissed.length - 1], status: "archived" }),
      });
    }
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ items }),
    });
  });

  return { saved: () => saved, dismissed: () => dismissed };
}

async function openApprovals(page: Page) {
  await page.goto("/approvals");
  const section = page.getByRole("region", { name: "Messages that didn't send" });
  await expect(section).toBeVisible({ timeout: 15_000 });
  return section;
}

test.describe("failed-send recovery (LUM-793)", () => {
  test.skip(!hasSmokeCreds, smokeCredsSkipMessage);

  test.beforeEach(async ({ page }) => {
    await loginWithSmokeCredentials(page);
  });

  test("an unsent message is listed with its body and can be saved to Drafts", async ({ page }) => {
    const rec = installMocks(page, [NOT_SENT]);
    const section = await openApprovals(page);

    // The text is there — this is the reassurance the whole feature is for.
    await expect(section.getByText("Saturday dinner")).toBeVisible();
    await expect(section.getByText("Shall we say 7pm at the usual place?")).toBeVisible();
    await expect(section.getByText(/Not sent/)).toBeVisible();

    await section.getByRole("button", { name: "Save to Drafts" }).click();
    await expect(section.getByText("Saved to Drafts.")).toBeVisible();
    expect(rec.saved()).toEqual([501]);
  });

  test("a partly-sent message names both sides and still offers recovery", async ({ page }) => {
    installMocks(page, [PARTIAL]);
    const section = await openApprovals(page);

    await expect(section.getByText(/Partly sent/)).toBeVisible();
    await expect(section.getByText(/bob@example\.com received it/)).toBeVisible();
    await expect(section.getByText(/carol@example\.com did not/)).toBeVisible();
    // Carol never got it, so recovery must stay open.
    await expect(section.getByRole("button", { name: "Save to Drafts" })).toBeVisible();
  });

  test("a message that actually sent offers no Save-to-Drafts", async ({ page }) => {
    installMocks(page, [SENT]);
    const section = await openApprovals(page);

    await expect(section.getByText(/Sent/)).toBeVisible();
    await expect(section.getByRole("button", { name: "Save to Drafts" })).toHaveCount(0);
    await expect(section.getByRole("button", { name: "Dismiss" })).toBeVisible();
  });

  test("dismiss removes the row", async ({ page }) => {
    const rec = installMocks(page, [NOT_SENT]);
    const section = await openApprovals(page);

    await section.getByRole("button", { name: "Dismiss" }).click();
    await expect(section.getByText("Saturday dinner")).toHaveCount(0);
    expect(rec.dismissed()).toEqual([501]);
  });
});

test.describe("failed-send recovery on mobile (LUM-793)", () => {
  test.use({ viewport: { width: 390, height: 844 } });
  test.skip(!hasSmokeCreds, smokeCredsSkipMessage);

  test.beforeEach(async ({ page }) => {
    await loginWithSmokeCredentials(page);
  });

  test("the row and both actions are usable on a phone", async ({ page }) => {
    installMocks(page, [NOT_SENT]);
    const section = await openApprovals(page);

    await expect(section.getByText("Shall we say 7pm at the usual place?")).toBeVisible();

    // Both buttons must be reachable and meet the tap-target floor — the
    // actions row wraps at this width, which is what the media query is for.
    for (const name of ["Save to Drafts", "Dismiss"]) {
      const button = section.getByRole("button", { name });
      await expect(button).toBeVisible();
      const box = await button.boundingBox();
      expect(box, `${name} has no layout box on mobile`).not.toBeNull();
      expect(box!.height).toBeGreaterThanOrEqual(44);
    }

    await section.getByRole("button", { name: "Save to Drafts" }).click();
    await expect(section.getByText("Saved to Drafts.")).toBeVisible();
  });
});
