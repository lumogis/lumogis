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

    await page.goto("/admin/system-status");
    await expect(page).toHaveURL(/\/admin\/system-status/);

    const section = page.locator("section").filter({
      has: page.getByRole("heading", { name: /^system status$/i, level: 2 }),
    });
    await expect(section).toBeVisible();

    await expect(section.getByText(/^loading/i)).toHaveCount(0, { timeout: 60_000 });

    await expect(
      section.getByRole("heading", { name: /^services$/i, level: 3 }).or(section.getByRole("alert")),
    ).toBeVisible({ timeout: 5_000 });

    const hasAlert = (await section.getByRole("alert").count()) > 0;
    if (!hasAlert) {
      await expect(section.getByRole("heading", { name: /^storage$/i, level: 3 })).toBeVisible();
      await expect(
        section.getByRole("heading", { name: /^ollama models$/i, level: 3 }),
      ).toBeVisible();
      await expect(section.getByRole("table").first()).toBeVisible();
    }
  });

  test("shows DR backup panel for admin", async ({ page }) => {
    await loginWithSmokeCredentials(page);

    await page.goto("/admin/system-status");
    await expect(page).toHaveURL(/\/admin\/system-status/);

    const section = page.locator("section").filter({
      has: page.getByRole("heading", { name: /^system status$/i, level: 2 }),
    });
    await expect(section).toBeVisible();

    await expect(section.getByText(/^loading/i)).toHaveCount(0, { timeout: 60_000 });

    await expect(
      section.getByRole("heading", { name: /^disaster recovery backup$/i, level: 3 }),
    ).toBeVisible({ timeout: 15_000 });

    await expect(section.getByText(/^enabled:/i)).toBeVisible();
  });

  test("admin nav links to system status", async ({ page }) => {
    await loginWithSmokeCredentials(page);

    await page.goto("/admin/users");
    await expect(page).toHaveURL(/\/admin\/users/);

    await page.getByRole("navigation", { name: /^administration$/i }).getByRole("link", {
      name: /^system status$/i,
    }).click();

    await expect(page).toHaveURL(/\/admin\/system-status/);
    await expect(page.getByRole("heading", { name: /^system status$/i, level: 2 })).toBeVisible();
  });
});
