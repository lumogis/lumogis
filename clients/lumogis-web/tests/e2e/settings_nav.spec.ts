// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// One Settings (LUM-993 Pass 3), against a running stack with smoke credentials:
// /settings/... loads and reloads as Lumogis Web while the legacy /settings API
// still answers JSON clients; every old address lands on its page (final URL and
// a visible h1 — a blank page fails); the Mine / Household switch changes the
// view; the phone index and back link; axe on Profile and the phone index.
//
// The admin assertions need an admin smoke account: LUMOGIS_E2E_EXPECT_ADMIN=1.
// The member path (household pages refused, no switch) needs DEMO_MEMBER_* and
// runs in the manual two-user lane; the unit tests cover it
// (tests/features/settings/SettingsLayout.test.tsx).

import { expect, test, type Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

import { hasSmokeCreds, loginWithSmokeCredentials, smokeCredsSkipMessage } from "./smoke-auth";

const EXPECT_ADMIN = process.env.LUMOGIS_E2E_EXPECT_ADMIN === "1";

// Old address → [new address, page title]. Mirrors settingsNav.legacyRedirectMap().
const OLD: Array<[string, string, string]> = [
  ["/me", "/settings/profile", "Profile"],
  ["/me/profile", "/settings/profile", "Profile"],
  ["/me/appearance", "/settings/appearance", "Look and feel"],
  ["/me/notifications", "/settings/notifications", "Notifications"],
  ["/me/email-signals", "/settings/email", "Email accounts"],
  ["/me/email-labels", "/settings/email-labels", "Email labels and rules"],
  ["/me/relevance", "/settings/what-matters", "What matters to you"],
  ["/me/sources", "/settings/sources", "Documents, feeds and pages"],
  ["/me/connectors", "/settings/connections", "Connected services"],
  ["/me/llm-providers", "/settings/ai-services", "AI services"],
  ["/me/mcp-tokens", "/settings/connected-apps", "Connected AI apps"],
  ["/me/permissions", "/settings/permissions", "What Lumogis may do"],
  ["/me/privacy-mode", "/settings/privacy", "Online or local AI"],
  ["/me/shared-items", "/settings/shared", "Shared with the household"],
  ["/me/wont-learn", "/settings/wont-learn", "Names Lumogis won't learn"],
  ["/me/export", "/settings/export", "Download my data"],
  ["/me/tools-capabilities", "/settings/tools", "Tools and capabilities"],
  ["/audit", "/settings/activity", "Activity"],
  ["/admin", "/settings/members", "Members and invites"],
  ["/admin/users", "/settings/members", "Members and invites"],
  ["/admin/connector-credentials", "/settings/connections?view=household", "Connected services"],
  ["/admin/system-mail", "/settings/system-mail", "Email Lumogis sends from"],
  ["/admin/connector-permissions", "/settings/permissions?view=household", "What Lumogis may do"],
  ["/admin/mcp-tokens", "/settings/connected-apps?view=household", "Connected AI apps"],
  ["/admin/shared-items", "/settings/shared?view=household", "Shared with the household"],
  ["/admin/audit", "/settings/activity?view=household", "Activity"],
  ["/admin/diagnostics", "/settings/diagnostics", "Diagnostics"],
  ["/admin/system-status", "/settings/health", "Health and backups"],
  ["/admin/privacy-mode", "/settings/privacy?view=household", "Online or local AI"],
  ["/admin/search-settings", "/settings/search-ranking", "Better search ranking"],
  ["/admin/knowledge-graph", "/settings/knowledge-graph", "Knowledge graph"],
  ["/admin/secure-address", "/settings/home-address", "Home address"],
  ["/admin/feature-flags", "/settings/feature-flags", "Feature flags"],
  ["/admin/safety", "/settings/safety", "Safety test bench"],
];

async function expectTitle(page: Page, title: string): Promise<void> {
  await expect(page.getByRole("heading", { level: 1 })).toHaveText(title, { timeout: 30_000 });
}

async function noSidewaysScroll(page: Page): Promise<void> {
  const over = await page.evaluate(() => {
    const main = document.getElementById("lumogis-main");
    return Math.max(document.body.scrollWidth - document.body.clientWidth, main ? main.scrollWidth - main.clientWidth : 0);
  });
  expect(over).toBeLessThanOrEqual(1);
}

async function axeClean(page: Page): Promise<void> {
  const axe = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa"]).analyze();
  const serious = axe.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
  expect(serious, JSON.stringify(serious, null, 2)).toHaveLength(0);
}

test.describe("Settings, wide screen", () => {
  test.skip(!hasSmokeCreds, smokeCredsSkipMessage);
  test.use({ viewport: { width: 1280, height: 900 } });

  test("loads and reloads as the app; the legacy /settings API still answers JSON", async ({ page }) => {
    await loginWithSmokeCredentials(page);
    await page.goto("/settings/profile");
    await expectTitle(page, "Profile");
    await page.reload();
    await expectTitle(page, "Profile");
    await expect(page.getByRole("navigation", { name: "Settings" })).toBeVisible();

    // A JSON client still reaches Core's /settings (any JSON answer, even 401, proves the route).
    const api = await page.request.get("/settings", { headers: { Accept: "application/json" } });
    expect(api.headers()["content-type"] ?? "").toContain("application/json");

    await noSidewaysScroll(page);
    await axeClean(page);
  });

  test("an admin sees every group, and old addresses land on their pages", async ({ page }) => {
    test.skip(!EXPECT_ADMIN, "needs an admin smoke account (LUMOGIS_E2E_EXPECT_ADMIN=1)");
    test.setTimeout(240_000);
    await loginWithSmokeCredentials(page);
    await page.goto("/settings/profile");
    const menu = page.getByRole("navigation", { name: "Settings" });
    for (const g of ["You", "Email and documents", "Connections", "Privacy and control", "Household"]) {
      await expect(menu.getByRole("heading", { name: g })).toBeVisible();
    }
    await expect(menu.getByRole("button", { name: "Advanced" })).toBeVisible();

    for (const [from, to, title] of OLD) {
      await page.goto(from);
      await expect(page, `${from}`).toHaveURL(new RegExp(`${to.replace(/[?]/g, "\\?")}$`));
      await expectTitle(page, title);
    }
  });

  test("loading an old address through the server serves the app, which redirects; reload keeps the page", async ({ page }) => {
    test.skip(!EXPECT_ADMIN, "needs an admin smoke account (LUMOGIS_E2E_EXPECT_ADMIN=1)");
    await loginWithSmokeCredentials(page);
    for (const [from, to, title] of [
      ["/me/profile", "/settings/profile", "Profile"],
      ["/admin/users", "/settings/members", "Members and invites"],
    ] as const) {
      // A full page load of the old address: the server must serve the app, which redirects.
      await page.goto(from);
      await expect(page).toHaveURL(new RegExp(`${to}$`));
      await expectTitle(page, title);
      // Reload only once the app has finished signing in: each page load spends a
      // single-use refresh token, and a reload that aborts that request signs out.
      await page.reload();
      await expectTitle(page, title);
    }
  });

  test("the Mine / Household switch changes the view and the address", async ({ page }) => {
    test.skip(!EXPECT_ADMIN, "needs an admin smoke account (LUMOGIS_E2E_EXPECT_ADMIN=1)");
    await loginWithSmokeCredentials(page);
    await page.goto("/settings/activity");
    await expectTitle(page, "Activity");
    await page.getByRole("button", { name: "Household", exact: true }).click();
    await expect(page).toHaveURL(/\/settings\/activity\?view=household$/);
    await expect(page.getByRole("button", { name: "Household", exact: true })).toHaveAttribute("aria-pressed", "true");
    await page.getByRole("button", { name: "Mine", exact: true }).click();
    await expect(page).toHaveURL(/\/settings\/activity$/);
  });
});

test.describe("Body text font", () => {
  test.skip(!hasSmokeCreds, smokeCredsSkipMessage);

  test("no page renders body text in Martian Mono (LUM-993)", async ({ page }) => {
    await loginWithSmokeCredentials(page);
    for (const path of ["/chat", "/settings/email", "/settings/health"]) {
      await page.goto(path);
      await expect(page.getByRole("heading", { level: 1 })).toBeVisible({ timeout: 30_000 });
      const body = await page.evaluate(() => getComputedStyle(document.body).fontFamily);
      const main = await page.evaluate(() => getComputedStyle(document.getElementById("lumogis-main")!).fontFamily);
      for (const family of [body, main]) expect(family, path).not.toMatch(/Martian/i);
    }
  });
});

test.describe("Settings, phone", () => {
  test.skip(!hasSmokeCreds, smokeCredsSkipMessage);
  test.use({ viewport: { width: 390, height: 844 }, hasTouch: true });

  test("/settings is the grouped index; a page has a Settings back link", async ({ page }) => {
    await loginWithSmokeCredentials(page);
    await page.goto("/settings");
    await expectTitle(page, "Settings");
    await expect(page.getByRole("navigation", { name: "Settings" })).toBeHidden();
    await page.getByRole("link", { name: /^Profile/ }).click();
    await expect(page).toHaveURL(/\/settings\/profile$/);
    await expectTitle(page, "Profile");
    await page.getByTestId("settings-layout").getByRole("link", { name: "Settings", exact: true }).click();
    await expect(page).toHaveURL(/\/settings$/);
    await noSidewaysScroll(page);
    await axeClean(page);
  });
});
