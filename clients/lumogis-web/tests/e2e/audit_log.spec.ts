// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Member audit log — LUM-197. Same creds as first_slice; skips without smoke env.

import { test, expect } from "@playwright/test";

import { hasSmokeCreds, loginWithSmokeCredentials, smokeCredsSkipMessage } from "./smoke-auth";

test.describe("Lumogis Web member audit log", () => {
  test.skip(!hasSmokeCreds, smokeCredsSkipMessage);

  test("audit: /settings/activity page renders heading and table or empty state", async ({ page }) => {
    await loginWithSmokeCredentials(page);
    await page.goto("/settings/activity");
    // The page is titled "Activity" (Settings, LUM-993 Pass 3); the household view
    // is the same page with ?view=household.
    await expect(page.getByRole("heading", { level: 1, name: "Activity" })).toBeVisible();
    const table = page.getByRole("table");
    const empty = page.getByText(/no audit events match your filters/i);
    await expect(table.or(empty)).toBeVisible();
  });

  test("audit: Privacy filter chip triggers refetch", async ({ page }) => {
    await loginWithSmokeCredentials(page);
    await page.goto("/settings/activity");
    await page.getByRole("button", { name: /^privacy$/i }).click();
    await expect(page).toHaveURL(/event_type=privacy/);
  });
});
