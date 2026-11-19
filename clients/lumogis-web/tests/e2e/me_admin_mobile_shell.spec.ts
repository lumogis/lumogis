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

  test("/settings/profile: phone page with a Settings back link, main visible, no page horizontal overflow", async ({ page }) => {
    await loginWithSmokeCredentials(page);
    await page.goto("/settings/profile");
    await expect(page).toHaveURL(/\/settings\/profile$/);

    const main = page.locator("#lumogis-main");
    await expect(main).toBeVisible();
    await expect(main.getByRole("heading", { level: 1, name: "Profile" })).toBeVisible();

    // On a phone the side menu is hidden; the page has a "Settings" back link to the grouped index.
    await expect(page.getByRole("navigation", { name: "Settings" })).toBeHidden();
    await expect(page.getByTestId("settings-layout").getByRole("link", { name: "Settings", exact: true })).toBeVisible();
    await page.getByTestId("settings-layout").getByRole("link", { name: "Settings", exact: true }).click();
    await expect(page).toHaveURL(/\/settings$/);
    await expect(page.getByRole("link", { name: /^Download my data/ })).toBeVisible();
    await page.goBack();

    await expectNoPageHorizontalOverflow(page);
  });

  test("/settings/members: phone page + no page horizontal overflow when user is admin", async ({
    page,
  }) => {
    await loginWithSmokeCredentials(page);
    await page.goto("/settings/members");
    await page.waitForURL(/\/settings\/(members|profile)$/, { timeout: 60_000 });
    // A non-admin is sent on to Profile once the user loads; wait for a title so it has settled.
    await page.getByRole("heading", { level: 1 }).waitFor({ timeout: 60_000 });

    if (!page.url().includes("/settings/members")) {
      test.skip(true, "Smoke user is not admin — cannot assert the members page on a phone.");
    }

    const main = page.locator("#lumogis-main");
    await expect(main).toBeVisible();
    await expect(main.getByRole("heading", { level: 1, name: "Members and invites" })).toBeVisible();

    // Household pages are listed in the phone index for an admin.
    await expect(page.getByTestId("settings-layout").getByRole("link", { name: "Settings", exact: true })).toBeVisible();
    await page.getByTestId("settings-layout").getByRole("link", { name: "Settings", exact: true }).click();
    await expect(page.getByRole("link", { name: /^Health and backups/ })).toBeVisible();
    await page.goBack();

    await expectNoPageHorizontalOverflow(page);
  });
});
