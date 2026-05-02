// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Minimal Playwright smoke for verify-public-rc: no credentials, no live LLM/STT.
// Requires Lumogis Web + Caddy at PLAYWRIGHT_BASE_URL (default http://127.0.0.1).

import { test, expect } from "@playwright/test";

import { attachRcMonitoring, assertRcHealthy } from "./helpers/rcHarness";

test.describe("public RC smoke (desktop)", () => {
  test("front door and login shell load without uncaught errors", async ({ page }) => {
    const mon = attachRcMonitoring(page);
    await page.goto("/", { waitUntil: "domcontentloaded" });
    await expect(page.locator("#root")).toBeVisible({ timeout: 60_000 });
    await expect(page.getByLabel("Email")).toBeVisible({ timeout: 60_000 });
    assertRcHealthy(mon);
  });

  test("chat route shows login or app shell", async ({ page }) => {
    const mon = attachRcMonitoring(page);
    await page.goto("/chat", { waitUntil: "domcontentloaded" });
    await expect(page.locator("#root")).toBeVisible({ timeout: 60_000 });
    const login = page.getByLabel("Email");
    const shell = page.getByTestId("lumogis-shell");
    await expect(login.or(shell).first()).toBeVisible({ timeout: 60_000 });
    assertRcHealthy(mon);
  });

  test("settings route shows login or settings chrome", async ({ page }) => {
    const mon = attachRcMonitoring(page);
    await page.goto("/me/profile", { waitUntil: "domcontentloaded" });
    await expect(page.locator("#root")).toBeVisible({ timeout: 60_000 });
    const login = page.getByLabel("Email");
    const settingsNav = page.getByRole("navigation", { name: /^settings$/i });
    await expect(login.or(settingsNav).first()).toBeVisible({ timeout: 60_000 });
    assertRcHealthy(mon);
  });
});

test.describe("public RC smoke (mobile viewport)", () => {
  test.use({ viewport: { width: 390, height: 844 } });

  test("front door loads on narrow viewport", async ({ page }) => {
    const mon = attachRcMonitoring(page);
    await page.goto("/", { waitUntil: "domcontentloaded" });
    await expect(page.locator("#root")).toBeVisible({ timeout: 60_000 });
    await expect(page.getByLabel("Email")).toBeVisible({ timeout: 60_000 });
    assertRcHealthy(mon);
  });
});
