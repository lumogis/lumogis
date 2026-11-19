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

/** Navigate to /settings/members; returns true when the admin panel actually loaded
 * (a non-admin smoke user is sent to Profile — mirrors the API 403). */
async function gotoAdminUsers(page: Page): Promise<boolean> {
  await loginWithSmokeCredentials(page);
  await page.goto("/settings/members");
  await page.waitForURL(/\/settings\/(members|profile)$/, { timeout: 60_000 });
  // A non-admin is sent on to Profile once the user loads; wait for a title so it has settled.
  await page.getByRole("heading", { level: 1 }).waitFor({ timeout: 60_000 });
  return page.url().includes("/settings/members");
}

test.describe("LUM-545 mobile admin users table", () => {
  test.use({ viewport: { width: 390, height: 844 } });

  test.skip(!hasSmokeCreds, smokeCredsSkipMessage);

  test("users table renders with member-count summary, no horizontal overflow", async ({ page }) => {
    if (!(await gotoAdminUsers(page))) {
      test.skip(true, "Smoke user is not admin — cannot assert /settings/members mobile table.");
      return;
    }

    const main = page.locator("#lumogis-main");
    await expect(main).toBeVisible();
    await expect(main.getByRole("heading", { level: 1, name: "Members and invites" })).toBeVisible();
    await expect(page.getByRole("table")).toBeVisible();
    // Member-count summary: "<n> member(s) · <m> admin(s)".
    await expect(page.getByText(/\d+ members? · \d+ admins?/)).toBeVisible();

    await expectNoPageHorizontalOverflow(page);
  });

  test("promote/demote asks for confirmation (cancel-only — no mutation)", async ({ page }) => {
    if (!(await gotoAdminUsers(page))) {
      test.skip(true, "Smoke user is not admin.");
      return;
    }

    // Open the first row's "…" menu and pick the role item, which must be enabled.
    await page.getByRole("button", { name: /more actions/i }).first().click();
    const roleItem = page.getByRole("menuitem", { name: /^make (admin|member)$/i }).first();
    if ((await roleItem.getAttribute("aria-disabled")) === "true") {
      // No eligible row (single-user household, or only the last admin present).
      test.skip(true, "No enabled promote/demote control available in this household.");
      return;
    }

    // The confirmation is an alert dialog. CANCEL it so no role change reaches the live stack.
    await roleItem.click();
    const dialog = page.getByRole("alertdialog");
    await expect(dialog).toBeVisible();
    await dialog.getByRole("button", { name: "Cancel" }).click();
    await expect(dialog).toBeHidden();
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
    // The dialog has an X and a footer Close; Escape closes it either way.
    await page.keyboard.press("Escape");
    await expect(page.getByRole("dialog")).toBeHidden();

    await expectNoPageHorizontalOverflow(page);
  });
});
