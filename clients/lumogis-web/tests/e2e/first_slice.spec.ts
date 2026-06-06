// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// End-to-end first slice — parent plan Phase 1 Pass 1.5 step 17.
// Requires Caddy + lumogis-web + orchestrator (docker compose up).
//
// Credentials (same as integration smoke):
//   export LUMOGIS_WEB_SMOKE_EMAIL=...
//   export LUMOGIS_WEB_SMOKE_PASSWORD='...'   # ≥12 chars
//
// Optional: PLAYWRIGHT_BASE_URL=http://other-host

import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

import { hasSmokeCreds, loginWithSmokeCredentials, smokeCredsSkipMessage } from "./smoke-auth";

test.describe("Lumogis Web first slice", () => {
  test.skip(!hasSmokeCreds, smokeCredsSkipMessage);

  test("login, land on chat, navigate search, axe main", async ({ page }) => {
    test.setTimeout(180_000);
    await loginWithSmokeCredentials(page);
    await expect(page.getByRole("navigation", { name: /primary navigation/i })).toBeVisible();

    await page.getByRole("link", { name: /^search$/i }).click({ force: true });
    await expect(page).toHaveURL(/\/search$/);
    await expect(page.getByRole("textbox", { name: /search query/i })).toBeVisible();

    const axe = await new AxeBuilder({ page })
      .include("#lumogis-main")
      .withTags(["wcag2a", "wcag2aa"])
      .analyze();
    const serious = axe.violations.filter(
      (v) => v.impact === "serious" || v.impact === "critical",
    );
    expect(
      serious,
      `a11y (serious/critical in #lumogis-main): ${JSON.stringify(serious, null, 2)}`,
    ).toHaveLength(0);
  });
});
