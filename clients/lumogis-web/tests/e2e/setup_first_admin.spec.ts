// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-981 — the first visitor creates the admin account, in a real browser.
//
// Runs only against an UNCLAIMED Lumogis (a fresh checkout stack: `cp .env.example .env`,
// `docker compose up -d`, nobody has opened it yet). Skipped unless LUMOGIS_E2E_SETUP_BASE_URL
// is set, because the claim happens once per install and cannot be repeated against a shared
// smoke stack.
//
//   LUMOGIS_E2E_SETUP_BASE_URL=http://localhost:8090 \
//   LUMOGIS_E2E_SETUP_EMAIL=admin@example.com LUMOGIS_E2E_SETUP_PASSWORD='<12+ chars>' \
//     npx playwright test tests/e2e/setup_first_admin.spec.ts --grep '1280'
//
// Run it once per viewport and tear the stack down with `-v` between the two runs, so each run
// starts unclaimed. Both describe blocks claim the admin.
//
// NOT covered here, on purpose (the vacuous-test principle): that exactly one admin can ever be
// created. That is a server rule, proven at the database level in
// orchestrator/tests/integration/test_instance_setup_live.py (L1-L3). This file asserts only
// what the browser does: the redirect, the form, the signed-in shell, and the reload.
//
// The overflow measure is the `document.body` form on purpose (see forgot_password.spec.ts):
// the `documentElement` form reads 0 under `overflow-x: hidden`.

import { test, expect } from "@playwright/test";

const baseURL = process.env.LUMOGIS_E2E_SETUP_BASE_URL ?? "";
const email = process.env.LUMOGIS_E2E_SETUP_EMAIL ?? "admin@example.com";
const password = process.env.LUMOGIS_E2E_SETUP_PASSWORD ?? "correct-horse-battery-staple";

const VIEWPORTS = [
  { label: "1280", viewport: { width: 1280, height: 800 } },
  { label: "390", viewport: { width: 390, height: 844 } },
] as const;

test.skip(!baseURL, "Set LUMOGIS_E2E_SETUP_BASE_URL to an unclaimed Lumogis to run the setup spec.");

for (const { label, viewport } of VIEWPORTS) {
  test.describe(`LUM-981 first-admin setup (${label})`, () => {
    // One install, one claim: the three steps share it and must run in order.
    test.describe.configure({ mode: "serial" });
    test.use({ baseURL, viewport });

    test("P3 the setup page fits the viewport", async ({ page }) => {
      await page.goto("/setup");
      await expect(page.getByRole("heading", { name: "Create the admin account" })).toBeVisible();
      const fits = await page.evaluate(() => document.body.scrollWidth <= document.body.clientWidth);
      expect(fits).toBe(true);
    });

    test("P1 the first visitor lands on setup, creates the admin and stays signed in", async ({ page }) => {
      await page.goto("/");
      await expect(page).toHaveURL(/\/setup$/);
      await expect(page.getByRole("heading", { name: "Create the admin account" })).toBeVisible();

      await page.getByLabel("Email").fill(email);
      await page.getByLabel("Password", { exact: true }).fill(password);
      await page.getByLabel("Confirm password").fill(password);
      await page.getByRole("button", { name: "Create account" }).click();

      await expect(page).toHaveURL(/\/chat$/, { timeout: 60_000 });
      await expect(page.getByTestId("lumogis-shell")).toBeVisible({ timeout: 60_000 });
      // OnboardingGate runs for the new admin.
      await expect(page.getByRole("dialog")).toBeVisible({ timeout: 30_000 });

      await page.reload();
      await expect(page.getByTestId("lumogis-shell")).toBeVisible({ timeout: 60_000 });
      await expect(page.getByRole("heading", { name: "Sign in" })).toHaveCount(0);
    });

    test("P2 a new browser context finds setup closed and sees the sign-in form", async ({ browser }) => {
      const context = await browser.newContext({ baseURL, viewport });
      try {
        const page = await context.newPage();
        await page.goto("/setup");
        await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
        await expect(page.getByRole("heading", { name: "Create the admin account" })).toHaveCount(0);
        await expect(page).not.toHaveURL(/\/setup$/);
      } finally {
        await context.close();
      }
    });
  });
}
