// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Admin / Me shell — FP-046. Same creds as first_slice; skips without smoke env.
// Optional: LUMOGIS_E2E_EXPECT_ADMIN=1 to assert admin table (fails for non-admin users).

import { test, expect } from "@playwright/test";

const email = process.env.LUMOGIS_WEB_SMOKE_EMAIL ?? "";
const password = process.env.LUMOGIS_WEB_SMOKE_PASSWORD ?? "";
const hasCreds = Boolean(email && password.length >= 12);
const requireCreds = process.env.E2E_REQUIRE_CREDS === "1";

if (requireCreds && !hasCreds) {
  throw new Error(
    "E2E_REQUIRE_CREDS=1 requires LUMOGIS_WEB_SMOKE_EMAIL and LUMOGIS_WEB_SMOKE_PASSWORD (≥12 chars).",
  );
}

test.describe("Lumogis Web me / admin shell", () => {
  test.skip(
    !hasCreds,
    "Set LUMOGIS_WEB_SMOKE_EMAIL and LUMOGIS_WEB_SMOKE_PASSWORD (≥12 chars) for e2e.",
  );

  test("me: Settings nav and Profile sub-route", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByLabel("Email")).toBeVisible();
    await page.getByLabel("Email").fill(email);
    await page.getByLabel("Password", { exact: true }).fill(password);
    await page.getByRole("button", { name: /^sign in$/i }).click();
    await expect(page).toHaveURL(/\/chat$/, { timeout: 60_000 });
    await expect(page.getByTestId("lumogis-shell")).toBeVisible({ timeout: 60_000 });

    await page.goto("/me");
    await expect(page).toHaveURL(/\/me\/profile/);
    await expect(page.getByRole("navigation", { name: /^settings$/i })).toBeVisible();
    await expect(page.getByRole("link", { name: /^connectors$/i })).toBeVisible();
  });

  test("admin: /admin either shows Users (admin) or leaves admin shell (user)", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByLabel("Email")).toBeVisible();
    await page.getByLabel("Email").fill(email);
    await page.getByLabel("Password", { exact: true }).fill(password);
    await page.getByRole("button", { name: /^sign in$/i }).click();
    await expect(page).toHaveURL(/\/chat$/, { timeout: 60_000 });
    await page.goto("/admin");
    if (page.url().includes("/admin")) {
      await expect(page.getByRole("heading", { name: "Users" })).toBeVisible();
    } else {
      await expect(page).toHaveURL(/\/chat/);
    }
  });
});

if (process.env.LUMOGIS_E2E_EXPECT_ADMIN === "1") {
  test.describe("smoke is admin (opt-in)", () => {
    test("admin area shows Users for LUMOGIS_E2E_EXPECT_ADMIN=1", async ({ page }) => {
      test.skip(!hasCreds, "creds");
      await page.goto("/");
      await page.getByLabel("Email").fill(email);
      await page.getByLabel("Password", { exact: true }).fill(password);
      await page.getByRole("button", { name: /^sign in$/i }).click();
      await expect(page).toHaveURL(/\/chat$/, { timeout: 60_000 });
      await page.goto("/admin");
      await expect(page).toHaveURL(/\/admin/);
      await expect(page.getByRole("heading", { name: "Users" })).toBeVisible();
    });
  });
}
