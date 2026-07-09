// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Parameterised login for two-user household Playwright proofs (LUM-582/583/585).

import { expect, type Page } from "@playwright/test";

import { dismissOnboardingIfPresent } from "./smoke-auth";

export const adminCreds = {
  email: process.env.LUMOGIS_WEB_SMOKE_EMAIL ?? "",
  password: process.env.LUMOGIS_WEB_SMOKE_PASSWORD ?? "",
};

export const memberCreds = {
  email: process.env.DEMO_MEMBER_EMAIL ?? "",
  password: process.env.DEMO_MEMBER_PASSWORD ?? "",
};

export const hasTwoUserCreds =
  adminCreds.email.length > 0 &&
  adminCreds.password.length >= 12 &&
  memberCreds.email.length > 0 &&
  memberCreds.password.length >= 12;

export const twoUserCredsSkipMessage =
  "Set LUMOGIS_WEB_SMOKE_* (admin) and DEMO_MEMBER_* (member, ≥12 chars) for two-user live proofs.";

export async function loginAs(page: Page, email: string, password: string): Promise<void> {
  await page.goto("/");
  await expect(page.getByLabel("Email")).toBeVisible();
  await page.getByLabel("Email").fill(email);
  const pw = page.getByLabel("Password", { exact: true });
  await pw.fill(password);
  await pw.press("Enter");
  await page.waitForURL(/\/chat$/, { timeout: 60_000 });
  await dismissOnboardingIfPresent(page);
}
