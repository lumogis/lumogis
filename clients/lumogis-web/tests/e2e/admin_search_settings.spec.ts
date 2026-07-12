// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-159 — admin Search & retrieval settings (BGE reranker toggle).
// Read-only smoke: load panel, nav link, RAM warning on BGE select, save disabled when clean.
// Does not save or restart the stack.
// Live-proven 2026-07-10 on lumogis-test stack (4/4); matrix row 2.2.16.
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

    await page.goto("/admin/search-settings");
    await expect(page).toHaveURL(/\/admin\/search-settings/);

    const section = page.getByTestId("retrieval-settings-page");
    await expect(section).toBeVisible();

    await expect(section.getByText(/^loading/i)).toHaveCount(0, { timeout: 60_000 });

    await expect(section.getByRole("heading", { name: /^search & retrieval$/i, level: 1 })).toBeVisible();
    await expect(section.getByRole("radio", { name: /off — default, lower ram/i })).toBeVisible();
    await expect(section.getByRole("radio", { name: /bge reranker/i })).toBeVisible();
    await expect(section.getByRole("button", { name: /^save retrieval settings$/i })).toBeVisible();
  });

  test("admin nav links to search & retrieval", async ({ page }) => {
    await loginWithSmokeCredentials(page);

    await page.goto("/admin/users");
    await expect(page).toHaveURL(/\/admin\/users/);

    await page
      .getByRole("navigation", { name: /^administration$/i })
      .getByRole("link", { name: /^search & retrieval$/i })
      .click();

    await expect(page).toHaveURL(/\/admin\/search-settings/);
    await expect(page.getByTestId("retrieval-settings-page")).toBeVisible();
    await expect(
      page.getByRole("heading", { name: /^search & retrieval$/i, level: 1 }),
    ).toBeVisible();
  });

  test("selecting BGE shows RAM warning without saving", async ({ page }) => {
    await loginWithSmokeCredentials(page);

    await page.goto("/admin/search-settings");
    const section = page.getByTestId("retrieval-settings-page");
    await expect(section.getByText(/^loading/i)).toHaveCount(0, { timeout: 60_000 });

    const bgeRadio = section.getByRole("radio", { name: /bge reranker/i });
    const offRadio = section.getByRole("radio", { name: /off — default, lower ram/i });

    if (await bgeRadio.isChecked()) {
      await offRadio.check();
      await expect(section.getByRole("note")).toHaveCount(0);
    }

    await bgeRadio.check();
    await expect(bgeRadio).toBeChecked();

    await expect(section.getByRole("note")).toContainText(/memory-constrained host/i);
    await expect(section.getByText(/unsaved change/i)).toBeVisible();
  });

  test("save is disabled until settings are dirty", async ({ page }) => {
    await loginWithSmokeCredentials(page);

    await page.goto("/admin/search-settings");
    const section = page.getByTestId("retrieval-settings-page");
    await expect(section.getByText(/^loading/i)).toHaveCount(0, { timeout: 60_000 });

    const saveButton = section.getByRole("button", { name: /^save retrieval settings$/i });
    await expect(saveButton).toBeDisabled();

    const bgeRadio = section.getByRole("radio", { name: /bge reranker/i });
    const offRadio = section.getByRole("radio", { name: /off — default, lower ram/i });

    if (await bgeRadio.isChecked()) {
      await offRadio.check();
    } else {
      await bgeRadio.check();
    }

    await expect(saveButton).toBeEnabled();

    if (await bgeRadio.isChecked()) {
      await offRadio.check();
    } else {
      await bgeRadio.check();
    }

    await expect(saveButton).toBeDisabled();
  });
});
