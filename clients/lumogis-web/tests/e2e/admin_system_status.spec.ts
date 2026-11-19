// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-178 / LUM-413 — admin System status panel (read-only stack health).
// LUM-487 — DR backup card on system status (Playwright).
// Requires smoke admin user and a running stack (orchestrator + optional stack-control).
//
//   export LUMOGIS_WEB_SMOKE_EMAIL=...
//   export LUMOGIS_WEB_SMOKE_PASSWORD='...'   # ≥12 chars
//   export LUMOGIS_E2E_EXPECT_ADMIN=1
//
// Optional: PLAYWRIGHT_BASE_URL=http://127.0.0.1

import { test, expect } from "@playwright/test";

import { hasSmokeCreds, loginWithSmokeCredentials, smokeCredsSkipMessage } from "./smoke-auth";

const expectAdmin =
  process.env.LUMOGIS_E2E_EXPECT_ADMIN === "1"
    ? ""
    : "Set LUMOGIS_E2E_EXPECT_ADMIN=1 (admin smoke user required).";

test.describe("LUM-178 admin system status", () => {
  test.skip(!hasSmokeCreds, smokeCredsSkipMessage);
  test.skip(!!expectAdmin, expectAdmin);

  test("system-status panel loads for admin", async ({ page }) => {
    await loginWithSmokeCredentials(page);

    await page.goto("/settings/health");
    await expect(page).toHaveURL(/\/settings\/health$/);
    await expect(page.getByRole("heading", { level: 1, name: "Health and backups" })).toBeVisible();

    const section = page.getByTestId("system-status");
    await expect(section).toBeVisible();

    await expect(section.getByText(/^loading/i)).toHaveCount(0, { timeout: 60_000 });

    await expect(
      section.getByRole("heading", { name: /^services$/i, level: 2 }).or(section.getByRole("alert")),
    ).toBeVisible({ timeout: 5_000 });

    const hasAlert = (await section.getByRole("alert").count()) > 0;
    if (!hasAlert) {
      await expect(section.getByRole("heading", { name: /^disk space$/i, level: 2 })).toBeVisible();
      await expect(
        section.getByRole("heading", { name: /^ollama models$/i, level: 2 }),
      ).toBeVisible();
      await expect(section.getByRole("table").first()).toBeVisible();
    }
  });

  test("shows DR backup panel for admin", async ({ page }) => {
    await loginWithSmokeCredentials(page);

    await page.goto("/settings/health");
    await expect(page).toHaveURL(/\/settings\/health$/);
    await expect(page.getByRole("heading", { level: 1, name: "Health and backups" })).toBeVisible();

    const section = page.getByTestId("system-status");
    await expect(section).toBeVisible();

    await expect(section.getByText(/^loading/i)).toHaveCount(0, { timeout: 60_000 });

    await expect(
      section.getByRole("heading", { name: /^backups$/i, level: 2 }),
    ).toBeVisible({ timeout: 15_000 });

    await expect(section.getByText(/^last backup$/i)).toBeVisible();

    // LUM-892: the card lists what the backup covers and what it does not. Which
    // items sit in which list is computed by Core (vacuous to assert here, see the
    // Vitest cases and test_backup_status_service.py), so only the headings are
    // checked. The DR card is not in the mobile Playwright project's matrix today.
    await expect(section.getByRole("heading", { name: /^covered$/i, level: 3 })).toBeVisible();
    await expect(section.getByRole("heading", { name: /^not covered$/i, level: 3 })).toBeVisible();
  });

  test("the Settings menu links to health and backups", async ({ page }) => {
    await loginWithSmokeCredentials(page);

    await page.goto("/settings/members");
    await expect(page).toHaveURL(/\/settings\/members$/);

    await page
      .getByRole("navigation", { name: "Settings" })
      .getByRole("link", { name: "Health and backups" })
      .click();

    await expect(page).toHaveURL(/\/settings\/health$/);
    await expect(page.getByRole("heading", { level: 1, name: "Health and backups" })).toBeVisible();
  });
});
