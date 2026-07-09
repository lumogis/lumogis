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
    await page.goto("/me/mcp-tokens");
    await expect(page).toHaveURL(/\/me\/mcp-tokens/);

    const main = page.locator("#lumogis-main");
    await expect(main.getByRole("heading", { name: /^mcp tokens$/i })).toBeVisible();

    // Least-privilege is the visible default: Read-only is pre-selected.
    const readOnly = page.getByRole("radio", { name: /^read-only$/i });
    const readWrite = page.getByRole("radio", { name: /^read \+ write$/i });
    await expect(readOnly).toBeVisible();
    await expect(readWrite).toBeVisible();
    await expect(readOnly).toBeChecked();

    // Mint a read-only token (leave the default selection).
    const label = `e2e-scope-${Date.now()}`;
    await page.getByPlaceholder(/label/i).fill(label);
    await page.getByRole("button", { name: /^mint$/i }).click();

    // The plaintext shows once in the copy-once modal.
    await expect(page.getByRole("heading", { name: /new mcp token/i })).toBeVisible();
    await page.getByRole("button", { name: /^close$/i }).click();

    // The new token appears in the list tagged Read-only.
    const row = main.locator("li", { hasText: label });
    await expect(row).toBeVisible();
    await expect(row.getByText(/\[Read-only\]/)).toBeVisible();

    // Tidy up so the smoke account doesn't accumulate tokens.
    await row.getByRole("button", { name: /^revoke$/i }).click();
  });
});
