// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Phase 2A — Me/Admin compact sub-shell (viewport ~390×844). Same creds contract as admin_shell.spec.

import { test, expect } from "@playwright/test";

import { hasSmokeCreds, loginWithSmokeCredentials, smokeCredsSkipMessage } from "./smoke-auth";

/** Page-level horizontal overflow — allow 1px subpixel tolerance. */
async function expectNoPageHorizontalOverflow(page: import("@playwright/test").Page): Promise<void> {
  const { scrollW, clientW } = await page.evaluate(() => ({
    scrollW: document.documentElement.scrollWidth,
    clientW: document.documentElement.clientWidth,
  }));
  expect(scrollW, `scrollWidth ${scrollW} should not exceed clientWidth ${clientW} much`).toBeLessThanOrEqual(
    clientW + 1,
  );
}

test.describe("Phase 2A mobile Me/Admin sub-shell", () => {
  test.use({ viewport: { width: 390, height: 844 } });

  test.skip(!hasSmokeCreds, smokeCredsSkipMessage);

  test("/me/profile: compact nav, main visible, no page horizontal overflow", async ({ page }) => {
    await loginWithSmokeCredentials(page);
    await page.goto("/me/profile");
    await expect(page).toHaveURL(/\/me\/profile/);

    const main = page.locator("#lumogis-main");
    await expect(main).toBeVisible();
    await expect(main.getByRole("heading", { name: /^profile$/i })).toBeVisible();

    const settingsNav = page.getByRole("navigation", { name: /^settings$/i });
    await expect(settingsNav).toBeVisible();
    await expect(settingsNav.getByRole("link", { name: /^connectors$/i })).toBeVisible();
    await expect(settingsNav.getByRole("link", { name: /^export$/i })).toBeVisible();

    await expectNoPageHorizontalOverflow(page);
  });

  test("/admin/users: compact nav + no page horizontal overflow when user is admin", async ({
    page,
  }) => {
    await loginWithSmokeCredentials(page);
    await page.goto("/admin/users");
    await page.waitForURL(/\/admin\/|\/chat/, { timeout: 60_000 });

    if (!page.url().includes("/admin")) {
      test.skip(true, "Smoke user is not admin — cannot assert /admin mobile shell.");
    }

    const main = page.locator("#lumogis-main");
    await expect(main).toBeVisible();
    await expect(main.getByRole("heading", { name: "Users" })).toBeVisible();

    const adminNav = page.getByRole("navigation", { name: /^administration$/i });
    await expect(adminNav).toBeVisible();
    await expect(adminNav.getByRole("link", { name: /^diagnostics$/i })).toBeVisible();

    await expectNoPageHorizontalOverflow(page);
  });
});
