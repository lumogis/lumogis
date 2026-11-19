// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-159 — admin Search & retrieval settings (BGE reranker toggle).
// Read-only smoke: load panel, nav link, RAM warning on BGE select, save disabled when clean.
// Does not save or restart the stack.
// Live-proven 2026-07-10 on lumogis-test stack (4/4); matrix row 2.2.16.
// LUM-993 Pass 6: the radio pair became one on/off switch (no legend any more).
//
//   export LUMOGIS_WEB_SMOKE_EMAIL=...
//   export LUMOGIS_WEB_SMOKE_PASSWORD='...'   # ≥12 chars
//   export LUMOGIS_E2E_EXPECT_ADMIN=1
//
// Optional: PLAYWRIGHT_BASE_URL=http://127.0.0.1

import { test, expect } from "@playwright/test";

import { hasSmokeCreds, loginWithSmokeCredentials, smokeCredsSkipMessage } from "./smoke-auth";

const expectAdmin =
  process.env.LUMOGIS_E2E_EXPECT_ADMIN === "1"
    ? ""
    : "Set LUMOGIS_E2E_EXPECT_ADMIN=1 (admin smoke user required).";

test.describe("LUM-159 admin search & retrieval settings", () => {
  test.skip(!hasSmokeCreds, smokeCredsSkipMessage);
  test.skip(!!expectAdmin, expectAdmin);

  test("search-settings panel loads for admin", async ({ page }) => {
    await loginWithSmokeCredentials(page);

    await page.goto("/settings/search-ranking");
    await expect(page).toHaveURL(/\/settings\/search-ranking$/);

    const section = page.getByTestId("retrieval-settings-page");
    await expect(section).toBeVisible();

    await expect(section.getByText(/^loading/i)).toHaveCount(0, { timeout: 60_000 });

    await expect(page.getByRole("heading", { name: "Better search ranking", level: 1 })).toBeVisible();
    await expect(section.getByRole("switch", { name: /better search ranking/i })).toBeVisible();

    await expect(section.getByRole("button", { name: /^save retrieval settings$/i })).toBeVisible();
  });

  test("the Settings menu links to better search ranking (Advanced group)", async ({ page }) => {
    await loginWithSmokeCredentials(page);

    await page.goto("/settings/members");
    await expect(page).toHaveURL(/\/settings\/members$/);

    const menu = page.getByRole("navigation", { name: "Settings" });
    // Advanced is collapsed unless the current page is inside it.
    await menu.getByRole("button", { name: "Advanced" }).click();
    await menu.getByRole("link", { name: "Better search ranking" }).click();

    await expect(page).toHaveURL(/\/settings\/search-ranking$/);
    await expect(page.getByTestId("retrieval-settings-page")).toBeVisible();
    await expect(page.getByRole("heading", { name: "Better search ranking", level: 1 })).toBeVisible();
  });

  test("selecting BGE shows RAM warning without saving", async ({ page }) => {
    await loginWithSmokeCredentials(page);

    await page.goto("/settings/search-ranking");
    const section = page.getByTestId("retrieval-settings-page");
    await expect(section.getByText(/^loading/i)).toHaveCount(0, { timeout: 60_000 });

    const sw = section.getByRole("switch", { name: /better search ranking/i });

    if ((await sw.getAttribute("aria-checked")) === "true") {
      await sw.click();
      await expect(section.getByRole("note")).toHaveCount(0);
    }

    await sw.click();
    await expect(sw).toHaveAttribute("aria-checked", "true");

    await expect(section.getByRole("note")).toContainText(/memory-constrained host/i);
    await expect(section.getByText(/unsaved change/i)).toBeVisible();
  });

  test("save is disabled until settings are dirty", async ({ page }) => {
    await loginWithSmokeCredentials(page);

    await page.goto("/settings/search-ranking");
    const section = page.getByTestId("retrieval-settings-page");
    await expect(section.getByText(/^loading/i)).toHaveCount(0, { timeout: 60_000 });

    const saveButton = section.getByRole("button", { name: /^save retrieval settings$/i });
    await expect(saveButton).toBeDisabled();

    const sw = section.getByRole("switch", { name: /better search ranking/i });

    await sw.click();
    await expect(saveButton).toBeEnabled();

    await sw.click();

    await expect(saveButton).toBeDisabled();
  });
});
