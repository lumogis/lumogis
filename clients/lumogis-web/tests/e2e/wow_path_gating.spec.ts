// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-216 — wow cards on /chat only; dismiss persists.

import { test, expect, type Page } from "@playwright/test";

import {
  ensureOnboardingCompletedAt,
  resetWowDismissedAt,
  seedWowEntitiesForSmokeUser,
} from "./e2e-postgres";
import {
  dismissOnboardingIfPresent,
  hasSmokeCreds,
  loginWithSmokeCredentials,
  smokeCredsSkipMessage,
  smokeEmail,
} from "./smoke-auth";

async function expectWowCardsVisible(page: Page): Promise<void> {
  await expect(page.getByTestId("wow-guided-card")).toBeVisible({ timeout: 30_000 });
}

async function expectWowCardsHidden(page: Page): Promise<void> {
  await expect(page.getByTestId("wow-guided-card")).toHaveCount(0, { timeout: 30_000 });
}

test.describe("LUM-216 wow path gating", () => {
  test.describe.configure({ mode: "serial" });

  test.skip(!hasSmokeCreds, smokeCredsSkipMessage);

  test.beforeEach(() => {
    resetWowDismissedAt(smokeEmail);
    ensureOnboardingCompletedAt(smokeEmail);
    seedWowEntitiesForSmokeUser(smokeEmail, 3);
  });

  test("wow cards appear on /chat but not on /search", async ({ page }) => {
    await loginWithSmokeCredentials(page);
    await dismissOnboardingIfPresent(page);

    await page.goto("/search");
    await expect(page.getByTestId("lumogis-shell")).toBeVisible({ timeout: 60_000 });
    await expectWowCardsHidden(page);

    await page.goto("/chat");
    await expect(page.getByTestId("chat-page")).toBeVisible({ timeout: 60_000 });
    await expectWowCardsVisible(page);
  });

  test("Dismiss hides cards after reload", async ({ page }) => {
    await loginWithSmokeCredentials(page);
    await dismissOnboardingIfPresent(page);

    await page.goto("/chat");
    await expectWowCardsVisible(page);

    await page
      .getByTestId("wow-guided-card")
      .getByRole("button", { name: /^dismiss$/i })
      .click({ force: true });
    await expectWowCardsHidden(page);

    await page.reload();
    await expect(page.getByTestId("chat-page")).toBeVisible({ timeout: 60_000 });
    await expectWowCardsHidden(page);
  });
});
