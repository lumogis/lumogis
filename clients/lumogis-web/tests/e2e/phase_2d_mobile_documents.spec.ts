// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-499 — Phase 2D: /documents library reachable from the mobile bottom nav,
// rendering its list/empty state without horizontal overflow at ~390×844.
// Same smoke-creds contract as 2A / 2B / 2C.

import { test, expect } from "@playwright/test";

import { hasSmokeCreds, loginWithSmokeCredentials, smokeCredsSkipMessage } from "./smoke-auth";

async function expectNoPageHorizontalOverflow(page: import("@playwright/test").Page): Promise<void> {
  const { scrollW, clientW } = await page.evaluate(() => ({
    scrollW: document.documentElement.scrollWidth,
    clientW: document.documentElement.clientWidth,
  }));
  expect(scrollW).toBeLessThanOrEqual(clientW + 1);
}

test.describe("Phase 2D mobile documents library (/documents)", () => {
  test.use({ viewport: { width: 390, height: 844 } });

  test.skip(!hasSmokeCreds, smokeCredsSkipMessage);

  test("Library reachable from bottom nav; renders without horizontal overflow", async ({ page }) => {
    await loginWithSmokeCredentials(page);

    // Start from a known shell route, then reach /documents via the mobile bottom nav
    // (the .lumogis-shell__bottom region is hidden ≥720px via container query).
    await page.goto("/chat");

    const bottomNav = page.locator(".lumogis-shell__bottom");
    const libraryLink = bottomNav.getByRole("link", { name: /^library$/i });
    await expect(libraryLink).toBeVisible();

    await libraryLink.click();
    await expect(page).toHaveURL(/\/documents$/);

    await expect(page.locator("#lumogis-main")).toBeVisible();
    await expect(
      page.getByTestId("documents-page").or(page.getByText("No documents yet")),
    ).toBeVisible({ timeout: 15_000 });

    await expectNoPageHorizontalOverflow(page);
  });
});
