// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-520 — Household admin users panel smoke (desktop).
// Smoke-credential-gated against a live stack (same pattern as admin_shell.spec.ts);
// it does NOT route-mock the API. The confirm-dialog assertion is CANCEL-ONLY so the
// shared smoke env's household roles are never mutated — the accept→PATCH /
// cancel→no-PATCH branches are proven exhaustively in the Vitest unit suite.

import { test, expect } from "@playwright/test";

import { hasSmokeCreds, loginWithSmokeCredentials, smokeCredsSkipMessage } from "./smoke-auth";

test.describe("Lumogis Web admin users panel (LUM-520)", () => {
  test.skip(!hasSmokeCreds, smokeCredsSkipMessage);

  test("admin: users table renders with member-count summary", async ({ page }) => {
    await loginWithSmokeCredentials(page);
    await page.goto("/settings/members");
    // Let any client-side admin-gate redirect settle before reading the URL —
    // a non-admin is sent to Profile (mirrors the API 403).
    await page.waitForURL(/\/settings\/(members|profile)$/, { timeout: 60_000 });
    // A non-admin is sent on to Profile once the user loads; wait for a title so it has settled.
    await page.getByRole("heading", { level: 1 }).waitFor({ timeout: 60_000 });

    // Only assert the panel contents when we actually landed on it.
    if (!page.url().includes("/settings/members")) {
      test.skip(true, "smoke user is not an admin; set LUMOGIS_E2E_EXPECT_ADMIN to require admin");
      return;
    }

    await expect(page.getByRole("heading", { level: 1, name: "Members and invites" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Invite member" })).toBeVisible();
    await expect(page.getByRole("table")).toBeVisible();
    // Member-count summary: "<n> member(s) · <m> admin(s)".
    await expect(page.getByText(/\d+ members? · \d+ admins?/)).toBeVisible();
  });

  test("row action controls are fully visible at desktop width (no clipping)", async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 800 });
    await loginWithSmokeCredentials(page);
    await page.goto("/settings/members");
    await page.waitForURL(/\/settings\/(members|profile)$/, { timeout: 60_000 });
    // A non-admin is sent on to Profile once the user loads; wait for a title so it has settled.
    await page.getByRole("heading", { level: 1 }).waitFor({ timeout: 60_000 });
    if (!page.url().includes("/settings/members")) {
      test.skip(true, "smoke user is not an admin");
      return;
    }

    const scroll = page.getByTestId("admin-users-table-scroll");
    await expect(scroll).toBeVisible();

    const moreBtn = page.getByRole("button", { name: /more actions/i }).first();
    await expect(moreBtn).toBeVisible();
    const box = await moreBtn.boundingBox();
    expect(box).not.toBeNull();
    const viewport = page.viewportSize();
    expect(viewport).not.toBeNull();
    if (box && viewport) {
      expect(box.x + box.width).toBeLessThanOrEqual(viewport.width);
      expect(box.x).toBeGreaterThanOrEqual(0);
    }
  });

  test("promote/demote asks for confirmation (cancel-only — no mutation)", async ({ page }) => {
    await loginWithSmokeCredentials(page);
    await page.goto("/settings/members");
    await page.waitForURL(/\/settings\/(members|profile)$/, { timeout: 60_000 });
    // A non-admin is sent on to Profile once the user loads; wait for a title so it has settled.
    await page.getByRole("heading", { level: 1 }).waitFor({ timeout: 60_000 });
    if (!page.url().includes("/settings/members")) {
      test.skip(true, "smoke user is not an admin");
      return;
    }

    // Open the first row's "…" menu and pick the role item, which must be enabled.
    await page.getByRole("button", { name: /more actions/i }).first().click();
    const roleItem = page.getByRole("menuitem", { name: /^make (admin|member)$/i }).first();
    if ((await roleItem.getAttribute("aria-disabled")) === "true") {
      // No eligible row (e.g. single-user household, or only the last admin present).
      test.skip(true, "no enabled promote/demote control available in this household");
      return;
    }

    // The confirmation is an alert dialog. CANCEL it so no role change reaches the live stack.
    await roleItem.click();
    const dialog = page.getByRole("alertdialog");
    await expect(dialog).toBeVisible();
    await dialog.getByRole("button", { name: "Cancel" }).click();
    await expect(dialog).toBeHidden();
  });
});
