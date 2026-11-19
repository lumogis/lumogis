// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Settings shell — FP-046, rewritten for the single Settings (LUM-993 Pass 3).
// Same creds as first_slice; skips without smoke env.
// Optional: LUMOGIS_E2E_EXPECT_ADMIN=1 to assert the household pages (fails for non-admin users).

import { test, expect } from "@playwright/test";

import { hasSmokeCreds, loginWithSmokeCredentials, smokeCredsSkipMessage } from "./smoke-auth";

test.describe("Lumogis Web Settings shell", () => {
  test.skip(!hasSmokeCreds, smokeCredsSkipMessage);

  test("old /me opens Profile in Settings, with the Settings menu", async ({ page }) => {
    await loginWithSmokeCredentials(page);

    await page.goto("/me");
    await expect(page).toHaveURL(/\/settings\/profile$/);
    await expect(page.getByRole("heading", { level: 1, name: "Profile" })).toBeVisible();
    await expect(page.getByRole("navigation", { name: "Settings" })).toBeVisible();
    await expect(page.getByRole("link", { name: "Connected services" })).toBeVisible();
  });

  test("old /admin opens Members and invites (admin) or Profile (member)", async ({ page }) => {
    await loginWithSmokeCredentials(page);
    await page.goto("/admin");
    await page.getByRole("heading", { level: 1 }).waitFor({ timeout: 60_000 });
    if (page.url().includes("/settings/members")) {
      await expect(page.getByRole("heading", { level: 1, name: "Members and invites" })).toBeVisible();
    } else {
      await expect(page).toHaveURL(/\/settings\/profile$/);
    }
  });
});

if (process.env.LUMOGIS_E2E_EXPECT_ADMIN === "1") {
  test.describe("smoke is admin (opt-in)", () => {
    test("household pages open for LUMOGIS_E2E_EXPECT_ADMIN=1", async ({ page }) => {
      test.skip(!hasSmokeCreds, "creds");
      await loginWithSmokeCredentials(page);
      await page.goto("/settings/members");
      await expect(page).toHaveURL(/\/settings\/members$/);
      await expect(page.getByRole("heading", { level: 1, name: "Members and invites" })).toBeVisible();
    });
  });
}
