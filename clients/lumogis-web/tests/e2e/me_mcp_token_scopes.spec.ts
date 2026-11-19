// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-527 — Me → MCP tokens: scope selector at mint + per-token access label.
// Same smoke-creds contract as the other e2e specs: skips without
// LUMOGIS_WEB_SMOKE_EMAIL / _PASSWORD; runs under `npm run e2e:prove`.

import { test, expect } from "@playwright/test";

import { hasSmokeCreds, loginWithSmokeCredentials, smokeCredsSkipMessage } from "./smoke-auth";

test.describe("Me MCP token scope selection (LUM-527)", () => {
  test.skip(!hasSmokeCreds, smokeCredsSkipMessage);

  test("mints a read-only token via the scope selector and shows its access label", async ({ page }) => {
    await loginWithSmokeCredentials(page);
    await page.goto("/settings/connected-apps");
    await expect(page).toHaveURL(/\/settings\/connected-apps$/);

    const main = page.locator("#lumogis-main");
    await expect(main.getByRole("heading", { level: 1, name: "Connected AI apps" })).toBeVisible();

    // Least-privilege is the visible default: Read-only is pre-selected.
    const readOnly = page.getByRole("radio", { name: /^read-only$/i });
    const readWrite = page.getByRole("radio", { name: /^read \+ write$/i });
    await expect(readOnly).toBeVisible();
    await expect(readWrite).toBeVisible();
    await expect(readOnly).toBeChecked();

    // Create a read-only key (leave the default selection).
    const label = `e2e-scope-${Date.now()}`;
    await page.getByLabel("Key name").fill(label);
    await page.getByRole("button", { name: /^create a key$/i }).click();

    // The key shows once in the dialog.
    await expect(page.getByRole("dialog").getByRole("heading", { name: /your new key/i })).toBeVisible();
    await page.getByRole("button", { name: /^close$/i }).click();

    // The new key appears in the list, described as Read-only.
    const row = main.locator("li", { hasText: label });
    await expect(row).toBeVisible();
    await expect(row.getByText(/Read-only/)).toBeVisible();

    // Tidy up so the smoke account doesn't accumulate keys: Revoke is in the row menu and confirmed.
    await row.getByRole("button", { name: `Actions for ${label}` }).click();
    await page.getByRole("menuitem", { name: /^revoke$/i }).click();
    await page.getByRole("alertdialog").getByRole("button", { name: /^revoke$/i }).click();
    await expect(row).toHaveCount(0);
  });
});
