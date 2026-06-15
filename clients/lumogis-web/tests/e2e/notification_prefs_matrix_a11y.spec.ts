// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-481 — axe scan of the editable notification-preferences matrix on /me/notifications.
// Requires Caddy + lumogis-web + orchestrator (docker compose up) with LUM-93 prefs UI shipped.
//
// Credentials (same as first_slice / integration smoke):
//   export LUMOGIS_WEB_SMOKE_EMAIL=...
//   export LUMOGIS_WEB_SMOKE_PASSWORD='...'   # ≥12 chars
//
// Optional: PLAYWRIGHT_BASE_URL=http://other-host

import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

import { hasSmokeCreds, loginWithSmokeCredentials, smokeCredsSkipMessage } from "./smoke-auth";

const PREFS_MATRIX_SELECTOR = '[aria-label="Notification preference matrix"]';

test.describe("LUM-481 notification prefs matrix a11y", () => {
  test.skip(!hasSmokeCreds, smokeCredsSkipMessage);

  test("login, open /me/notifications, axe prefs matrix table", async ({ page }) => {
    test.setTimeout(180_000);
    await loginWithSmokeCredentials(page);

    await page.goto("/me/notifications");
    await expect(page).toHaveURL(/\/me\/notifications$/);
    await expect(page.getByRole("heading", { name: /^notifications$/i })).toBeVisible();

    const prefsTable = page.getByRole("table", { name: /notification preference matrix/i });
    await expect(prefsTable).toBeVisible({ timeout: 60_000 });

    const axe = await new AxeBuilder({ page })
      .include(PREFS_MATRIX_SELECTOR)
      .withTags(["wcag2a", "wcag2aa"])
      .analyze();
    const serious = axe.violations.filter(
      (v) => v.impact === "serious" || v.impact === "critical",
    );
    expect(
      serious,
      `a11y (serious/critical in notification prefs matrix): ${JSON.stringify(serious, null, 2)}`,
    ).toHaveLength(0);
  });
});
