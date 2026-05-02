// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Phase 2B — high-traffic surfaces at ~390×844. Same smoke creds contract as first_slice / 2A.

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

async function login(page: import("@playwright/test").Page): Promise<void> {
  await page.goto("/");
  await expect(page.getByLabel("Email")).toBeVisible();
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password", { exact: true }).fill(password);
  await page.getByRole("button", { name: /^sign in$/i }).click();
  await expect(page).toHaveURL(/\/chat$/, { timeout: 60_000 });
}

async function expectNoPageHorizontalOverflow(page: import("@playwright/test").Page): Promise<void> {
  const { scrollW, clientW } = await page.evaluate(() => ({
    scrollW: document.documentElement.scrollWidth,
    clientW: document.documentElement.clientWidth,
  }));
  expect(scrollW).toBeLessThanOrEqual(clientW + 1);
}

test.describe("Phase 2B mobile surfaces (/approvals, /chat, /search)", () => {
  test.use({ viewport: { width: 390, height: 844 } });

  test.skip(!hasCreds, "Set LUMOGIS_WEB_SMOKE_EMAIL and LUMOGIS_WEB_SMOKE_PASSWORD (≥12 chars) for e2e.");

  test("main regions visible; no document-level horizontal overflow", async ({ page }) => {
    await login(page);

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
