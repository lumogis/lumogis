// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-315 — first-run onboarding dismiss persists after reload (LUM-165).
// Same smoke creds contract as first_slice / admin_shell.
//
// Reset approach: before each test, clear `users.onboarding_completed_at` for the
// smoke user via host psql (CI publishes postgres) or compose exec fallback.

import { test, expect, type Page } from "@playwright/test";

import { resetOnboardingCompletedAt } from "./e2e-postgres";
import {
  hasSmokeCreds,
  loginWithSmokeCredentials,
  smokeCredsSkipMessage,
  smokeEmail,
} from "./smoke-auth";

async function expectOnboardingModalVisible(page: Page): Promise<void> {
  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible({ timeout: 30_000 });
  await expect(dialog.getByRole("heading", { name: "Welcome" })).toBeVisible();
}

async function expectOnboardingModalHidden(page: Page): Promise<void> {
  await expect(page.getByRole("dialog")).toHaveCount(0, { timeout: 30_000 });
}

test.describe("LUM-315 onboarding dismiss persists after reload", () => {
  test.describe.configure({ mode: "serial" });

  test.skip(!hasSmokeCreds, smokeCredsSkipMessage);

  test.beforeEach(() => {
    resetOnboardingCompletedAt(smokeEmail);
  });

  test("Skip dismisses onboarding and modal stays hidden after reload", async ({ page }) => {
    await loginWithSmokeCredentials(page, { dismissOnboarding: false });
    await expectOnboardingModalVisible(page);

    await page.getByRole("button", { name: /^skip$/i }).click({ force: true });
    await expectOnboardingModalHidden(page);

    await page.reload();
    await expect(page.getByTestId("lumogis-shell")).toBeVisible({ timeout: 60_000 });
    await expectOnboardingModalHidden(page);
  });

  // LUM-282 / LUM-854. The handoff replaced a button that navigated behind an
  // overlay that never closed. This asserts the modal goes, the page is
  // reachable, onboarding stays completed, and that focus lands on the Sources
  // heading. (Until LUM-993 Pass 7 it also simulated the old frame's late
  // `inert` release; the ui Dialog has no such window.)
  test("the handoff completes onboarding and lands on a usable /settings/sources", async ({ page }) => {
    await loginWithSmokeCredentials(page, { dismissOnboarding: false });
    await expectOnboardingModalVisible(page);

    const dialog = page.getByRole("dialog");
    await dialog.getByRole("button", { name: /^next$/i }).click({ force: true });
    await dialog.getByRole("button", { name: /^next$/i }).click({ force: true });
    const handoff = dialog.getByRole("button", { name: /Set up documents now/ });
    await expect(handoff).toBeVisible();

    // LUM-993 Pass 7: the onboarding modal is a ui Dialog now. The old hand-made
    // frame set `inert` on #root and released it a tick late, which is the race
    // the Sources heading's focus retry existed for; this test used to hold that
    // `inert` to prove the retry. The Dialog never sets `inert` on #root, so the
    // simulation no longer applies. What the person needs is still checked
    // below: after the hand-off, focus lands on the Sources page heading.
    await handoff.click({ force: true });
    await expect(page).toHaveURL(/\/settings\/sources$/, { timeout: 60_000 });

    await expectOnboardingModalHidden(page);
    await expect(page.getByRole("heading", { name: "Add a source" })).toBeVisible({
      timeout: 15_000,
    });
    await expect(page.locator("#root")).not.toHaveAttribute("inert", /.*/);

    await expect
      .poll(() => page.evaluate(() => document.activeElement?.tagName ?? null), {
        timeout: 10_000,
      })
      .toBe("H1");
    expect(
      await page.evaluate(() => document.activeElement?.textContent?.trim() ?? null),
    ).toBe("Documents, feeds and pages");
    await page.reload();
    await expect(page.getByTestId("lumogis-shell")).toBeVisible({ timeout: 60_000 });
    await expectOnboardingModalHidden(page);
  });

  test("Done dismisses onboarding and modal stays hidden after reload", async ({ page }) => {
    await loginWithSmokeCredentials(page, { dismissOnboarding: false });
    await expectOnboardingModalVisible(page);

    const dialog = page.getByRole("dialog");
    await dialog.getByRole("button", { name: /^next$/i }).click({ force: true });
    await expect(dialog.getByRole("heading", { name: "Add knowledge" })).toBeVisible();
    await dialog.getByRole("button", { name: /^next$/i }).click({ force: true });
    await expect(dialog.getByRole("heading", { name: "Connect sources" })).toBeVisible();
    await dialog.getByRole("button", { name: /^next$/i }).click({ force: true });
    await expect(dialog.getByRole("heading", { name: "Done" })).toBeVisible();

    await dialog.getByRole("button", { name: /^done$/i }).click({ force: true });
    await expectOnboardingModalHidden(page);

    await page.reload();
    await expect(page.getByTestId("lumogis-shell")).toBeVisible({ timeout: 60_000 });
    await expectOnboardingModalHidden(page);
  });
});
