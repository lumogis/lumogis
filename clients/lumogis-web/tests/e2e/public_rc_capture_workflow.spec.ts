// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis

import { test, expect } from "@playwright/test";

import { attachRcMonitoring, assertRcHealthy } from "./helpers/rcHarness";
import { hasRcSmokeCreds, signInRcSmoke } from "./helpers/rcSignedIn";

test.beforeEach(() => {
  test.skip(!hasRcSmokeCreds, "Set LUMOGIS_WEB_SMOKE_EMAIL and LUMOGIS_WEB_SMOKE_PASSWORD (≥12 chars).");
});

test("submit text capture to server", async ({ page }) => {
  const mon = attachRcMonitoring(page);
  await signInRcSmoke(page);
  await page.goto("/capture", { waitUntil: "domcontentloaded" });

  await expect(page.getByRole("heading", { name: /^Quick capture$/i })).toBeVisible({ timeout: 60_000 });

  const note = `rc-cap-e2e-${Date.now()}`;
  await page.getByLabel(/^Note$/).fill(note);
  await page.getByTestId("quick-capture-save-server").click();

  await expect(page.getByTestId("quick-capture-info")).toContainText(/capture saved/i, {
    timeout: 60_000,
  });
  await expect(page.getByTestId("quick-capture-error")).toHaveCount(0);

  assertRcHealthy(mon);
});
