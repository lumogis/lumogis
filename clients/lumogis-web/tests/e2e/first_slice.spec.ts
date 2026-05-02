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

const email = process.env.LUMOGIS_WEB_SMOKE_EMAIL ?? "";
const password = process.env.LUMOGIS_WEB_SMOKE_PASSWORD ?? "";

const hasCreds = Boolean(email && password.length >= 12);
const requireCreds = process.env.E2E_REQUIRE_CREDS === "1";

if (requireCreds && !hasCreds) {
  throw new Error(
    "E2E_REQUIRE_CREDS=1 requires LUMOGIS_WEB_SMOKE_EMAIL and LUMOGIS_WEB_SMOKE_PASSWORD (≥12 chars). " +
      "Boot the stack (docker compose up -d), export those variables, then run make web-e2e-prove. " +
      "For a skip-ok local run without creds, use make web-e2e (no E2E_REQUIRE_CREDS).",
  );
}

test.describe("Lumogis Web first slice", () => {
  test.skip(
    !hasCreds,
    "Set LUMOGIS_WEB_SMOKE_EMAIL and LUMOGIS_WEB_SMOKE_PASSWORD (≥12 chars), or run make web-e2e-prove with E2E_REQUIRE_CREDS=1 and valid creds.",
  );

  test("login, land on chat, navigate search, axe main", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByLabel("Email")).toBeVisible();

    await page.getByLabel("Email").fill(email);
    await page.getByLabel("Password", { exact: true }).fill(password);
    await page.getByRole("button", { name: /^sign in$/i }).click();

    await expect(page).toHaveURL(/\/chat$/, { timeout: 60_000 });
    await expect(page.getByTestId("lumogis-shell")).toBeVisible({ timeout: 60_000 });
    await expect(page.getByRole("navigation", { name: /primary navigation/i })).toBeVisible();

    const axe = await new AxeBuilder({ page })
      .include("#lumogis-main")
      .analyze();
    const serious = axe.violations.filter(
      (v) => v.impact === "serious" || v.impact === "critical",
    );
    expect(
      serious,
      `a11y (serious/critical in #lumogis-main): ${JSON.stringify(serious, null, 2)}`,
    ).toHaveLength(0);

    await page.getByRole("link", { name: /^search$/i }).click();
    await expect(page).toHaveURL(/\/search$/);
    await expect(page.getByRole("textbox", { name: /search query/i })).toBeVisible();
  });
});
