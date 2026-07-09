// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-186 — household invite flow smoke (desktop).
// Gated on smoke credentials; dismiss-only where mutation would affect shared env.

import { test, expect } from "@playwright/test";

import { hasSmokeCreds, loginWithSmokeCredentials, smokeCredsSkipMessage } from "./smoke-auth";

test.describe("Lumogis Web household invite (LUM-186)", () => {
  test.skip(!hasSmokeCreds, smokeCredsSkipMessage);

  test("admin: invite member affordance visible", async ({ page }) => {
    await loginWithSmokeCredentials(page);
    await page.goto("/admin/users");
    await page.waitForURL(/\/admin\/|\/chat/, { timeout: 60_000 });
    if (!page.url().includes("/admin")) {
      test.skip(true, "smoke user is not an admin");
      return;
    }
    await expect(page.getByRole("button", { name: "Invite member" })).toBeVisible();
    await page.getByRole("button", { name: "Invite member" }).click();
    await expect(page.getByRole("heading", { name: "Invite member" })).toBeVisible();
    await page.getByRole("button", { name: "Close" }).click();
  });

  test("public invite page renders without auth", async ({ page }) => {
    await page.goto("/invite?token=linv_invalidsmoketoken");
    await expect(page.getByRole("heading", { name: "Join household" })).toBeVisible();
  });
});
