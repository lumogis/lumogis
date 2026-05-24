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

test.describe("Phase 2C mobile dense tables (/admin/users, /me/llm-providers)", () => {
  test.use({ viewport: { width: 390, height: 844 } });

  test.skip(!hasSmokeCreds, smokeCredsSkipMessage);

  test("main content visible; no document-level horizontal overflow", async ({ page }) => {
    await loginWithSmokeCredentials(page);

    await page.goto("/admin/users");
    await page.waitForURL(/\/admin\/|\/chat/, { timeout: 60_000 });
    if (!page.url().includes("/admin")) {
      test.skip(true, "Smoke user is not admin — skip /admin/users dense table check.");
    }
    await expect(page.locator("#lumogis-main")).toBeVisible();
    await expect(page.getByRole("heading", { name: "Users" })).toBeVisible();
    await expectNoPageHorizontalOverflow(page);

    await page.goto("/me/llm-providers");
    await expect(page).toHaveURL(/\/me\/llm-providers/);
    await expect(page.locator("#lumogis-main")).toBeVisible();
    await expect(page.getByRole("heading", { name: /^llm providers$/i })).toBeVisible();
    await expectNoPageHorizontalOverflow(page);
  });
});
