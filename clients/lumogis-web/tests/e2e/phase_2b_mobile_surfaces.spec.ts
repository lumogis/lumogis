// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Phase 2B — high-traffic surfaces at ~390×844. Same smoke creds contract as first_slice / 2A.

import { test, expect } from "@playwright/test";

import { hasSmokeCreds, loginWithSmokeCredentials, smokeCredsSkipMessage } from "./smoke-auth";

async function expectNoPageHorizontalOverflow(page: import("@playwright/test").Page): Promise<void> {
  const { scrollW, clientW } = await page.evaluate(() => ({
    scrollW: document.documentElement.scrollWidth,
    clientW: document.documentElement.clientWidth,
  }));
  expect(scrollW).toBeLessThanOrEqual(clientW + 1);
}

test.describe("Phase 2B mobile surfaces (/approvals, /chat, /search)", () => {
  test.use({ viewport: { width: 390, height: 844 } });

  test.skip(!hasSmokeCreds, smokeCredsSkipMessage);

  test("main regions visible; no document-level horizontal overflow", async ({ page }) => {
    await loginWithSmokeCredentials(page);

    for (const path of ["/approvals", "/chat", "/search"] as const) {
      await page.goto(path);
      await expect(page.locator("#lumogis-main")).toBeVisible();
      if (path === "/approvals") {
        await expect(page.getByRole("heading", { name: /^approvals$/i })).toBeVisible();
      }
      if (path === "/chat") {
        await expect(page.getByTestId("chat-page")).toBeVisible();
      }
      if (path === "/search") {
        await expect(page.getByRole("heading", { name: /^search$/i })).toBeVisible();
      }
      await expectNoPageHorizontalOverflow(page);
    }
  });
});
