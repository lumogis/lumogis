// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Signed-in navigation click-through (desktop + mobile). Skips when smoke credentials unset.

import { test, expect } from "@playwright/test";

import { attachRcMonitoring, assertRcHealthy } from "./helpers/rcHarness";
import { hasRcSmokeCreds, signInRcSmoke } from "./helpers/rcSignedIn";

const hasCreds = hasRcSmokeCreds;

async function signIn(page: import("@playwright/test").Page): Promise<void> {
  await signInRcSmoke(page);
}

test.describe("RC nav clickthrough — desktop", () => {
  test.beforeEach(() => {
    test.skip(!hasCreds, "Set LUMOGIS_WEB_SMOKE_EMAIL and LUMOGIS_WEB_SMOKE_PASSWORD (≥12 chars).");
  });

  test("primary nav + settings + search surface", async ({ page }) => {
    const mon = attachRcMonitoring(page);
    await signIn(page);

    const primary = page.getByRole("navigation", { name: /^primary navigation$/i });

    await primary.getByRole("link", { name: /^search$/i }).click();
    await expect(page).toHaveURL(/\/search$/, { timeout: 30_000 });

    await primary.getByRole("link", { name: /^capture$/i }).click();
    await expect(page).toHaveURL(/\/capture$/);

    await primary.getByRole("link", { name: /^approvals$/i }).click();
    await expect(page).toHaveURL(/\/approvals$/);

    await primary.getByRole("link", { name: /^chat$/i }).click();
    await expect(page).toHaveURL(/\/chat$/);

    await primary.getByRole("link", { name: /^settings$/i }).click();
    await expect(page).toHaveURL(/\/me\/profile/);
    await expect(page.getByRole("navigation", { name: /^settings$/i })).toBeVisible();
    await expect(page.getByRole("link", { name: /^connectors$/i })).toBeVisible();

    await primary.getByRole("link", { name: /^search$/i }).click();
    await expect(page.getByRole("searchbox", { name: /search query/i })).toBeVisible({ timeout: 30_000 });

    assertRcHealthy(mon);
  });

  test("admin diagnostics surface after login", async ({ page }) => {
    const mon = attachRcMonitoring(page);
    await signIn(page);
    await page.goto("/admin/diagnostics", { waitUntil: "domcontentloaded" });
    await expect(page.getByRole("heading", { name: /^Diagnostics$/i })).toBeVisible({ timeout: 60_000 });
    const summary = page.getByTestId("lumogis-admin-diagnostics");
    if (await summary.isVisible().catch(() => false)) {
      await expect(summary).toBeVisible();
    }
    assertRcHealthy(mon);
  });
});

test.describe("RC nav clickthrough — mobile viewport", () => {
  test.use({ viewport: { width: 390, height: 844 } });

  test.beforeEach(() => {
    test.skip(!hasCreds, "Set LUMOGIS_WEB_SMOKE_EMAIL and LUMOGIS_WEB_SMOKE_PASSWORD (≥12 chars).");
  });

  test("bottom primary navigation round-trip", async ({ page }) => {
    const mon = attachRcMonitoring(page);
    await signIn(page);

    const primary = page.getByRole("navigation", { name: /^primary navigation$/i });

    await primary.getByRole("link", { name: /^search$/i }).click();
    await expect(page).toHaveURL(/\/search$/);

    await primary.getByRole("link", { name: /^chat$/i }).click();
    await expect(page).toHaveURL(/\/chat$/);

    await primary.getByRole("link", { name: /^settings$/i }).click();
    await expect(page).toHaveURL(/\/me\/profile/);

    assertRcHealthy(mon);
  });
});
