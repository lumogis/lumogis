// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Shared smoke-user login for Playwright e2e (family-LAN creds from env).

import { expect, type Page } from "@playwright/test";

export const smokeEmail = process.env.LUMOGIS_WEB_SMOKE_EMAIL ?? "";
export const smokePassword = process.env.LUMOGIS_WEB_SMOKE_PASSWORD ?? "";
export const hasSmokeCreds = Boolean(smokeEmail && smokePassword.length >= 12);

const SMOKE_CREDS_SKIP =
  "Set LUMOGIS_WEB_SMOKE_EMAIL and LUMOGIS_WEB_SMOKE_PASSWORD (≥12 chars) for e2e.";

export const smokeCredsSkipMessage = SMOKE_CREDS_SKIP;

if (process.env.E2E_REQUIRE_CREDS === "1" && !hasSmokeCreds) {
  throw new Error(
    "E2E_REQUIRE_CREDS=1 requires LUMOGIS_WEB_SMOKE_EMAIL and LUMOGIS_WEB_SMOKE_PASSWORD (≥12 chars). " +
      "Boot the stack (docker compose up -d), export those variables, then run make web-e2e-prove. " +
      "For a skip-ok local run without creds, use make web-e2e (no E2E_REQUIRE_CREDS).",
  );
}

/** Dismiss first-run onboarding when the Welcome dialog is shown. No-op otherwise. */
export async function dismissOnboardingIfPresent(page: Page): Promise<void> {
  const skipButton = page.getByRole("dialog").getByRole("button", { name: /^skip$/i });
  try {
    await expect(skipButton).toBeVisible({ timeout: 5_000 });
  } catch {
    return;
  }
  await skipButton.click({ force: true });
  await expect(page.getByRole("dialog")).toHaveCount(0, { timeout: 30_000 });
}

export async function loginWithSmokeCredentials(
  page: Page,
  options: { dismissOnboarding?: boolean } = {},
): Promise<void> {
  const { dismissOnboarding = true } = options;
  await page.goto("/");
  await expect(page.getByLabel("Email")).toBeVisible();
  await page.getByLabel("Email").fill(smokeEmail);
  const password = page.getByLabel("Password", { exact: true });
  await password.fill(smokePassword);
  // Enter submits the form reliably; clicking the button can hang on actionability
  // when the password field keeps focus (headless Chromium stability checks).
  await password.press("Enter");
  await expect(page).toHaveURL(/\/chat$/, { timeout: 60_000 });
  await expect(page.getByTestId("lumogis-shell")).toBeVisible({ timeout: 60_000 });
  if (dismissOnboarding) {
    await dismissOnboardingIfPresent(page);
  }
}
