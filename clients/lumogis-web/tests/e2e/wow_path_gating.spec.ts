// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-216 — wow cards on /chat only; dismiss persists.
//
// Since LUM-993 Pass 2b the Chat home shows the library card instead while the
// library is empty, so each test gives the smoke user a one-document library
// (a mocked GET /api/v1/documents). Without it the spec passed only on stacks
// that happened to hold documents (a fresh release-candidate stack has none).

import { test, expect, type Page, type Route } from "@playwright/test";

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

async function withOneDocumentLibrary(page: Page): Promise<void> {
  await page.route("**/api/v1/documents", async (route: Route) => {
    if (route.request().method() !== "GET") return route.continue();
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        documents: [
          {
            document_id: 9_216,
            display_name: "household-notes.md",
            file_path: "/library/household-notes.md",
            file_type: "md",
            chunk_count: 3,
            entity_count: 3,
            scope: "personal",
            status: "indexed",
            indexed_at: "2026-11-19T08:00:00+00:00",
            error_message: null,
            share_status: "personal",
            in_flight_share_job_id: null,
            is_owner: true,
          },
        ],
      }),
    });
  });
}

async function expectWowCardsVisible(page: Page): Promise<void> {
  await expect(page.getByTestId("wow-guided-card")).toBeVisible({ timeout: 30_000 });
}

async function expectWowCardsHidden(page: Page): Promise<void> {
  await expect(page.getByTestId("wow-guided-card")).toHaveCount(0, { timeout: 30_000 });
}

test.describe("LUM-216 wow path gating", () => {
  test.describe.configure({ mode: "serial" });

  test.skip(!hasSmokeCreds, smokeCredsSkipMessage);

  test.beforeEach(async ({ page }) => {
    resetWowDismissedAt(smokeEmail);
    ensureOnboardingCompletedAt(smokeEmail);
    seedWowEntitiesForSmokeUser(smokeEmail, 3);
    await withOneDocumentLibrary(page);
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
