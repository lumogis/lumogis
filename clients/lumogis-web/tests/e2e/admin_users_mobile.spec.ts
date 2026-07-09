// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-545 — Household admin users table on mobile (viewport ~390×844).
// Companion to the desktop smoke (admin_users.spec.ts) and the mobile shell
// spec (me_admin_mobile_shell.spec.ts, which covers nav/overflow only).
// Smoke-credential-gated against a live stack; the confirm-dialog assertion is
// DISMISS-ONLY so the shared smoke env's household roles are never mutated — the
// accept→PATCH / cancel→no-PATCH branches are proven in the Vitest unit suite.
// Runs in the `chromium-smoke-shared-user` project (single worker — see
// playwright.config.ts: shared smoke user logins must not race).

import { test, expect, type Page } from "@playwright/test";

import { hasSmokeCreds, loginWithSmokeCredentials, smokeCredsSkipMessage } from "./smoke-auth";

/** Page-level horizontal overflow — allow 1px subpixel tolerance. */
async function expectNoPageHorizontalOverflow(page: Page): Promise<void> {
  const { scrollW, clientW } = await page.evaluate(() => ({
    scrollW: document.documentElement.scrollWidth,
    clientW: document.documentElement.clientWidth,
  }));
  expect(
    scrollW,
    `scrollWidth ${scrollW} should not exceed clientWidth ${clientW} much`,
  ).toBeLessThanOrEqual(clientW + 1);
}

/** Navigate to /admin/users; returns true when the admin panel actually loaded
 * (a non-admin smoke user is bounced to /chat — mirrors the API 403). */
async function gotoAdminUsers(page: Page): Promise<boolean> {
  await loginWithSmokeCredentials(page);
  await page.goto("/admin/users");
  await page.waitForURL(/\/admin\/|\/chat/, { timeout: 60_000 });
  return page.url().includes("/admin");
}

test.describe("LUM-545 mobile admin users table", () => {
  test.use({ viewport: { width: 390, height: 844 } });

  test.skip(!hasSmokeCreds, smokeCredsSkipMessage);

  test("users table renders with member-count summary, no horizontal overflow", async ({ page }) => {
    if (!(await gotoAdminUsers(page))) {
      test.skip(true, "Smoke user is not admin — cannot assert /admin/users mobile table.");
      return;
    }

    const main = page.locator("#lumogis-main");
    await expect(main).toBeVisible();
    await expect(main.getByRole("heading", { name: "Users" })).toBeVisible();
    await expect(page.getByRole("table")).toBeVisible();
    // Member-count summary: "<n> member(s) · <m> admin(s)".
    await expect(page.getByText(/\d+ members? · \d+ admins?/)).toBeVisible();

    await expectNoPageHorizontalOverflow(page);
  });

  test("promote/demote raises a confirm dialog (dismiss-only — no mutation)", async ({ page }) => {
    if (!(await gotoAdminUsers(page))) {
      test.skip(true, "Smoke user is not admin.");
      return;
    }

    const roleButton = page.getByRole("button", { name: /^make (admin|member)$/i }).first();
    if ((await roleButton.count()) === 0 || (await roleButton.isDisabled())) {
      // No eligible row (single-user household, or only the last admin present).
      test.skip(true, "No enabled promote/demote control available in this household.");
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

  test("invite member affordance visible (LUM-578)", async ({ page }) => {
    if (!(await gotoAdminUsers(page))) {
      test.skip(true, "Smoke user is not admin — cannot assert invite affordance.");
      return;
    }

    const inviteButton = page.getByRole("button", { name: "Invite member" });
    await expect(inviteButton).toBeVisible();
    await inviteButton.click();
    await expect(page.getByRole("heading", { name: "Invite member" })).toBeVisible();
    await page.getByRole("button", { name: "Close" }).click();

    await expectNoPageHorizontalOverflow(page);
  });
});
