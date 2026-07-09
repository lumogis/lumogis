// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-520 — Household admin users panel smoke (desktop).
// Smoke-credential-gated against a live stack (same pattern as admin_shell.spec.ts);
// it does NOT route-mock the API. The confirm-dialog assertion is DISMISS-ONLY so the
// shared smoke env's household roles are never mutated — the accept→PATCH /
// cancel→no-PATCH branches are proven exhaustively in the Vitest unit suite.

import { test, expect } from "@playwright/test";

import { hasSmokeCreds, loginWithSmokeCredentials, smokeCredsSkipMessage } from "./smoke-auth";

test.describe("Lumogis Web admin users panel (LUM-520)", () => {
  test.skip(!hasSmokeCreds, smokeCredsSkipMessage);

  test("admin: users table renders with member-count summary", async ({ page }) => {
    await loginWithSmokeCredentials(page);
    await page.goto("/admin/users");
    // Let any client-side admin-gate redirect settle before reading the URL —
    // a non-admin is bounced to /chat (mirrors the API 403).
    await page.waitForURL(/\/admin\/|\/chat/, { timeout: 60_000 });

    // Only assert the panel contents when we actually landed on it.
    if (!page.url().includes("/admin")) {
      test.skip(true, "smoke user is not an admin; set LUMOGIS_E2E_EXPECT_ADMIN to require admin");
      return;
    }

    await expect(page.getByRole("heading", { name: "Users" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Invite member" })).toBeVisible();
    await expect(page.getByRole("table")).toBeVisible();
    // Member-count summary: "<n> member(s) · <m> admin(s)".
    await expect(page.getByText(/\d+ members? · \d+ admins?/)).toBeVisible();
  });

  test("promote/demote raises a confirm dialog (dismiss-only — no mutation)", async ({ page }) => {
    await loginWithSmokeCredentials(page);
    await page.goto("/admin/users");
    await page.waitForURL(/\/admin\/|\/chat/, { timeout: 60_000 });
    if (!page.url().includes("/admin")) {
      test.skip(true, "smoke user is not an admin");
      return;
    }

    const roleButton = page.getByRole("button", { name: /^make (admin|member)$/i }).first();
    const count = await roleButton.count();
    if (count === 0 || (await roleButton.isDisabled())) {
      // No eligible row (e.g. single-user household, or only the last admin present).
      test.skip(true, "no enabled promote/demote control available in this household");
      return;
    }

    // Capture and DISMISS the confirm so no role change is committed to the live stack.
    let dialogSeen = false;
    page.once("dialog", (dialog) => {
      dialogSeen = true;
      void dialog.dismiss();
    });
    await roleButton.click();
    await expect.poll(() => dialogSeen).toBe(true);
  });
});
