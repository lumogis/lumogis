// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Member audit log — LUM-197. Same creds as first_slice; skips without smoke env.

import { test, expect } from "@playwright/test";

import { hasSmokeCreds, loginWithSmokeCredentials, smokeCredsSkipMessage } from "./smoke-auth";

test.describe("Lumogis Web member audit log", () => {
  test.skip(!hasSmokeCreds, smokeCredsSkipMessage);

  test("audit: /audit page renders heading and table or empty state", async ({ page }) => {
    await loginWithSmokeCredentials(page);
    await page.goto("/audit");
    // "My activity", not "Audit log": c06d376a3 renamed the member-facing page
    // to match MeNav and docs/capabilities.md. "Audit log" is now dead copy —
    // the admin surface at /admin/audit reads "Household audit" — so the old
    // assertion could never pass against any shipped page.
    await expect(page.getByRole("heading", { name: /my activity/i })).toBeVisible();
    const table = page.getByRole("table");
    const empty = page.getByText(/no audit events match your filters/i);
    await expect(table.or(empty)).toBeVisible();
  });

  test("audit: Privacy filter chip triggers refetch", async ({ page }) => {
    await loginWithSmokeCredentials(page);
    await page.goto("/audit");
    await page.getByRole("button", { name: /^privacy$/i }).click();
    await expect(page).toHaveURL(/event_type=privacy/);
  });
});
