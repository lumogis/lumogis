// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis

import { expect, type Page } from "@playwright/test";

export const rcSmokeEmail = process.env.LUMOGIS_WEB_SMOKE_EMAIL ?? "";
export const rcSmokePassword = process.env.LUMOGIS_WEB_SMOKE_PASSWORD ?? "";
export const hasRcSmokeCreds = Boolean(rcSmokeEmail && rcSmokePassword.length >= 12);

/** Family-LAN smoke login — must run single-worker when sharing one account. */
export async function signInRcSmoke(page: Page): Promise<void> {
  await page.goto("/", { waitUntil: "domcontentloaded" });
  await expect(page.getByLabel("Email")).toBeVisible({ timeout: 60_000 });
  await page.getByLabel("Email").fill(rcSmokeEmail);
  await page.getByLabel("Password", { exact: true }).fill(rcSmokePassword);
  await page.getByRole("button", { name: /^sign in$/i }).click();
  await expect(page).toHaveURL(/\/chat$/, { timeout: 120_000 });
  await expect(page.getByTestId("lumogis-shell")).toBeVisible({ timeout: 60_000 });
}
