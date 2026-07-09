// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-593 — Member audit log on mobile (viewport ~390×844).
// Companion to audit_log.spec.ts (desktop smoke, LUM-197).
// Smoke-credential-gated against a live stack.

import { test, expect, type Page } from "@playwright/test";

import { hasSmokeCreds, loginWithSmokeCredentials, smokeCredsSkipMessage } from "./smoke-auth";

/** Page-level horizontal overflow — allow 1px subpixel tolerance. */
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

test.describe("LUM-593 mobile member audit log", () => {
  test.use({ viewport: { width: 390, height: 844 } });

  test.skip(!hasSmokeCreds, smokeCredsSkipMessage);

  test("/audit: settings subshell, audit content, no horizontal overflow", async ({ page }) => {
    await loginWithSmokeCredentials(page);
    await page.goto("/audit");
    await expect(page).toHaveURL(/\/audit/);

    const main = page.locator("#lumogis-main");
    await expect(main).toBeVisible();
    await expect(main.getByRole("heading", { name: /^audit log$/i })).toBeVisible();

    const settingsNav = page.getByRole("navigation", { name: /^settings$/i });
    await expect(settingsNav).toBeVisible();
    await expect(settingsNav.getByRole("link", { name: /^audit log$/i })).toBeVisible();

    const table = page.getByRole("table");
    const empty = page.getByText(/no audit events match your filters/i);
    await expect(table.or(empty)).toBeVisible();

    await expectNoPageHorizontalOverflow(page);
  });
});
