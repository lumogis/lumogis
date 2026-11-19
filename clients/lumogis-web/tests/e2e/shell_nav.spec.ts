// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// The navigation shell (LUM-993 Pass 2a), against a running stack with smoke
// credentials: no header bar; the sidebar on a wide screen (places, collapse,
// account menu) and the tab bar with More on a phone (Settings as the fifth tab
// when More would hold nothing else; the bar steps aside while you type); Approvals shown only when
// /api/v1/me/shell says something can act (mocked both ways here, so the test
// does not depend on what the smoke account has connected).
//
// What the server computes for can_act is tested in orchestrator/tests/test_me_shell.py;
// this spec only checks that the shell follows the answer.

import { expect, test, type Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

import { hasSmokeCreds, loginWithSmokeCredentials, smokeCredsSkipMessage } from "./smoke-auth";

async function mockShell(page: Page, state: { can_act: boolean; pending_approvals: number }): Promise<void> {
  await page.route("**/api/v1/me/shell", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        email_connected: false,
        voice_available: false,
        online_ai_allowed: false,
        knowledge_graph_on: false,
        ...state,
      }),
    }),
  );
}

async function expectNoPageHorizontalOverflow(page: Page): Promise<void> {
  // Both the page and #lumogis-main, which is the scroll container since LUM-993
  // Pass 2a: wide content scrolls inside main and never widens body.
  const over = await page.evaluate(() => {
    const main = document.getElementById("lumogis-main");
    return Math.max(
      document.body.scrollWidth - document.body.clientWidth,
      main ? main.scrollWidth - main.clientWidth : 0,
    );
  });
  expect(over).toBeLessThanOrEqual(1);
}

test.describe("shell navigation, wide screen", () => {
  test.skip(!hasSmokeCreds, smokeCredsSkipMessage);
  test.use({ viewport: { width: 1280, height: 800 } });

  test("sidebar places, no header bar, collapse kept across a reload", async ({ page }) => {
    await mockShell(page, { can_act: false, pending_approvals: 0 });
    await loginWithSmokeCredentials(page);
    const nav = page.getByRole("navigation", { name: "Primary navigation" });
    await expect(nav).toBeVisible();
    await expect(page.locator(".lumogis-shell__header")).toHaveCount(0);
    for (const name of ["Chat", "Search", "Library", "Capture", "Settings"]) {
      await expect(nav.getByRole("link", { name, exact: true })).toBeVisible();
    }
    await expect(nav.getByRole("link", { name: /^Approvals/ })).toHaveCount(0);

    await nav.getByRole("link", { name: "Library", exact: true }).click();
    await expect(page).toHaveURL(/\/documents$/);
    await expect(nav.getByRole("link", { name: "Library", exact: true })).toHaveAttribute("aria-current", "page");

    await page.getByRole("button", { name: "Collapse sidebar" }).click();
    await page.reload();
    await expect(page.getByRole("button", { name: "Expand sidebar" })).toBeVisible();
    await page.getByRole("button", { name: "Expand sidebar" }).click();
    await expect(page.getByRole("button", { name: "Collapse sidebar" })).toBeVisible();
    await expectNoPageHorizontalOverflow(page);
  });

  test("Approvals appears with its count once something can act", async ({ page }) => {
    await mockShell(page, { can_act: true, pending_approvals: 2 });
    await loginWithSmokeCredentials(page);
    const link = page.getByRole("navigation", { name: "Primary navigation" }).getByRole("link", { name: "Approvals, 2 waiting" });
    await expect(link).toBeVisible();
    await link.click();
    await expect(page).toHaveURL(/\/approvals$/);
  });

  test("account menu offers Profile, Look and feel and Sign out; axe finds nothing serious", async ({ page }) => {
    await mockShell(page, { can_act: false, pending_approvals: 0 });
    await loginWithSmokeCredentials(page);
    await page.getByRole("button", { name: "Account menu" }).click();
    for (const name of ["Profile", "Look and feel", "Sign out"]) {
      await expect(page.getByRole("menuitem", { name })).toBeVisible();
    }
    await page.getByRole("menuitem", { name: "Look and feel" }).click();
    await expect(page).toHaveURL(/\/settings\/appearance$/);

    const axe = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa"]).analyze();
    const serious = axe.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
    expect(serious, JSON.stringify(serious, null, 2)).toHaveLength(0);
  });
});

test.describe("shell navigation, phone", () => {
  test.skip(!hasSmokeCreds, smokeCredsSkipMessage);
  test.use({ viewport: { width: 390, height: 844 }, hasTouch: true });

  test("four tabs and More; More holds the rest and is current on those pages", async ({ page }) => {
    await mockShell(page, { can_act: true, pending_approvals: 1 });
    await loginWithSmokeCredentials(page);
    const bar = page.getByRole("navigation", { name: "Primary navigation" });
    await expect(bar.getByRole("link")).toHaveText(["Chat", "Search", "Library", "Capture"]);
    await expect(page.getByRole("button", { name: "Collapse sidebar" })).toHaveCount(0);

    await bar.getByRole("button", { name: "More, 1 waiting" }).click();
    const sheet = page.getByRole("dialog", { name: "More" });
    await expect(sheet.getByRole("link", { name: /^Approvals/ })).toBeVisible();
    await expect(sheet.getByRole("button", { name: "Sign out" })).toBeVisible();
    await sheet.getByRole("link", { name: "Settings" }).click();
    await expect(sheet).toHaveCount(0);
    await expect(page).toHaveURL(/\/settings$/);
    await expect(bar.getByRole("button", { name: /^More/ })).toHaveAttribute("aria-current", "page");

    // Tab targets are at least 44 px tall (touch first).
    const box = await bar.getByRole("link", { name: "Chat" }).boundingBox();
    expect(box?.height ?? 0).toBeGreaterThanOrEqual(44);
    await expectNoPageHorizontalOverflow(page);
  });

  test("Settings is the fifth tab when More would hold only Settings, and has Sign out", async ({ page }) => {
    await mockShell(page, { can_act: false, pending_approvals: 0 });
    await loginWithSmokeCredentials(page);
    const bar = page.getByRole("navigation", { name: "Primary navigation" });
    await expect(bar.getByRole("link")).toHaveText(["Chat", "Search", "Library", "Capture", "Settings"]);
    await expect(bar.getByRole("button", { name: /^More/ })).toHaveCount(0);
    await bar.getByRole("link", { name: "Settings" }).click();
    await expect(page).toHaveURL(/\/settings$/);
    await expect(bar.getByRole("link", { name: "Settings" })).toHaveAttribute("aria-current", "page");
    await expect(page.getByTestId("settings-home").getByRole("button", { name: "Sign out" })).toBeVisible();
  });

  test("the tab bar steps aside while you type and comes back after", async ({ page }) => {
    await mockShell(page, { can_act: false, pending_approvals: 0 });
    await loginWithSmokeCredentials(page);
    await page.goto("/chat");
    const bar = page.getByRole("navigation", { name: "Primary navigation" });
    await expect(bar).toBeVisible();
    const input = page.getByTestId("chat-home").getByLabel("Message", { exact: true });
    await input.focus();
    await expect(bar).toBeHidden();
    await input.blur();
    await expect(bar).toBeVisible();
  });
});
