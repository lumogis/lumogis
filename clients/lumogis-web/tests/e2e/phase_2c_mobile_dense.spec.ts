// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Phase 2C — dense Admin / Me settings tables at ~390×844. Same smoke creds contract as 2A / 2B.

import { test, expect } from "@playwright/test";

import { hasSmokeCreds, loginWithSmokeCredentials, smokeCredsSkipMessage } from "./smoke-auth";

async function expectNoPageHorizontalOverflow(page: import("@playwright/test").Page): Promise<void> {
  const { scrollW, clientW } = await page.evaluate(() => ({
    scrollW: document.documentElement.scrollWidth,
    clientW: document.documentElement.clientWidth,
  }));
  expect(scrollW).toBeLessThanOrEqual(clientW + 1);
}

test.describe("Phase 2C mobile dense tables (/settings/members, /settings/ai-services)", () => {
  test.use({ viewport: { width: 390, height: 844 } });

  test.skip(!hasSmokeCreds, smokeCredsSkipMessage);

  test("main content visible; no document-level horizontal overflow", async ({ page }) => {
    await loginWithSmokeCredentials(page);

    await page.goto("/settings/members");
    await page.waitForURL(/\/settings\/(members|profile)$/, { timeout: 60_000 });
    // A non-admin is sent on to Profile once the user loads; wait for a title so it has settled.
    await page.getByRole("heading", { level: 1 }).waitFor({ timeout: 60_000 });
    if (!page.url().includes("/settings/members")) {
      test.skip(true, "Smoke user is not admin — skip /settings/members dense table check.");
    }
    await expect(page.locator("#lumogis-main")).toBeVisible();
    await expect(page.getByRole("heading", { level: 1, name: "Members and invites" })).toBeVisible();
    await expectNoPageHorizontalOverflow(page);

    await page.goto("/settings/ai-services");
    await expect(page).toHaveURL(/\/settings\/ai-services$/);
    await expect(page.locator("#lumogis-main")).toBeVisible();
    await expect(page.getByRole("heading", { level: 1, name: "AI services" })).toBeVisible();
    await expectNoPageHorizontalOverflow(page);
  });
});
