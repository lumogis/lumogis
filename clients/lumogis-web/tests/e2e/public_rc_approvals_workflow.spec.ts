// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Requires scripts/seed-public-rc-approvals-fixture (runs on RC compose up).

import { test, expect } from "@playwright/test";

import { attachRcMonitoring, assertRcHealthy } from "./helpers/rcHarness";
import { hasRcSmokeCreds, signInRcSmoke } from "./helpers/rcSignedIn";

test.beforeEach(() => {
  test.skip(!hasRcSmokeCreds, "Set LUMOGIS_WEB_SMOKE_EMAIL and LUMOGIS_WEB_SMOKE_PASSWORD (≥12 chars).");
});

test("resolve seeded denied action via connector DO mode", async ({ page }) => {
  const mon = attachRcMonitoring(page);
  await signInRcSmoke(page);

  await page.goto("/approvals", { waitUntil: "domcontentloaded" });
  await expect(page.getByRole("heading", { name: /^Approvals$/i })).toBeVisible({ timeout: 60_000 });

  const connectorCell = page.locator(".lumogis-approvals__connector").filter({ hasText: "filesystem-mcp" });
  await expect(connectorCell.first()).toBeVisible({ timeout: 60_000 });

  await page.getByRole("button", { name: /Switch filesystem-mcp to DO mode/i }).first().click();
  const dlg = page.getByRole("dialog");
  await expect(dlg).toBeVisible();
  await dlg.getByRole("button", { name: /Switch filesystem-mcp to DO mode/i }).click();

  await expect(page.getByRole("dialog")).toBeHidden({ timeout: 60_000 });
  await expect(page.getByRole("alert")).toHaveCount(0);

  assertRcHealthy(mon);
});
