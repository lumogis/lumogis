// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Deep-link smoke for primary SPA routes without credentials (expect login shell or app shell).

import { test, expect } from "@playwright/test";

import { attachRcMonitoring, assertRcHealthy } from "./helpers/rcHarness";

async function expectRootShellOrLogin(page: import("@playwright/test").Page): Promise<void> {
  await expect(page.locator("#root")).toBeVisible({ timeout: 60_000 });
  const login = page.getByLabel("Email");
  const shell = page.getByTestId("lumogis-shell");
  await expect(login.or(shell).first()).toBeVisible({ timeout: 60_000 });
}

test.describe("RC routes — desktop", () => {
  const routes = ["/chat", "/search", "/capture", "/approvals", "/me/profile", "/me/connectors"];

  for (const path of routes) {
    test(`GET ${path} renders`, async ({ page }) => {
      const mon = attachRcMonitoring(page);
      await page.goto(path, { waitUntil: "domcontentloaded" });
      await expectRootShellOrLogin(page);
      assertRcHealthy(mon);
    });
  }

  test("GET /admin/diagnostics renders (login, forbidden shell, or diagnostics)", async ({ page }) => {
    const mon = attachRcMonitoring(page);
    await page.goto("/admin/diagnostics", { waitUntil: "domcontentloaded" });
    await expect(page.locator("#root")).toBeVisible({ timeout: 60_000 });
    const login = page.getByLabel("Email");
    const diagHeading = page.getByRole("heading", { name: /^Diagnostics$/i });
    await expect(login.or(diagHeading).first()).toBeVisible({ timeout: 60_000 });
    assertRcHealthy(mon);
  });
});

test.describe("RC routes — mobile viewport", () => {
  test.use({ viewport: { width: 390, height: 844 } });

  test("primary routes resolve", async ({ page }) => {
    const mon = attachRcMonitoring(page);
    for (const path of ["/chat", "/search", "/capture"]) {
      await page.goto(path, { waitUntil: "domcontentloaded" });
      await expectRootShellOrLogin(page);
    }
    assertRcHealthy(mon);
  });
});
