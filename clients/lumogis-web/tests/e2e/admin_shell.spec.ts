// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Admin / Me shell — FP-046. Same creds as first_slice; skips without smoke env.
// Optional: LUMOGIS_E2E_EXPECT_ADMIN=1 to assert admin table (fails for non-admin users).

import { test, expect } from "@playwright/test";

import { hasSmokeCreds, loginWithSmokeCredentials, smokeCredsSkipMessage } from "./smoke-auth";

test.describe("Lumogis Web me / admin shell", () => {
  test.skip(!hasSmokeCreds, smokeCredsSkipMessage);

  test("me: Settings nav and Profile sub-route", async ({ page }) => {
    await loginWithSmokeCredentials(page);

    await page.goto("/me");
    await expect(page).toHaveURL(/\/me\/profile/);
    await expect(page.getByRole("navigation", { name: /^settings$/i })).toBeVisible();
    await expect(page.getByRole("link", { name: /^connectors$/i })).toBeVisible();
  });

  test("admin: /admin either shows Users (admin) or leaves admin shell (user)", async ({ page }) => {
    await loginWithSmokeCredentials(page);
    await page.goto("/admin");
    if (page.url().includes("/admin")) {
      await expect(page.getByRole("heading", { name: "Users" })).toBeVisible();
    } else {
      await expect(page).toHaveURL(/\/chat/);
    }
  });
});

if (process.env.LUMOGIS_E2E_EXPECT_ADMIN === "1") {
  test.describe("smoke is admin (opt-in)", () => {
    test("admin area shows Users for LUMOGIS_E2E_EXPECT_ADMIN=1", async ({ page }) => {
      test.skip(!hasSmokeCreds, "creds");
      await loginWithSmokeCredentials(page);
      await page.goto("/admin");
      await expect(page).toHaveURL(/\/admin/);
      await expect(page.getByRole("heading", { name: "Users" })).toBeVisible();
    });
  });
}
