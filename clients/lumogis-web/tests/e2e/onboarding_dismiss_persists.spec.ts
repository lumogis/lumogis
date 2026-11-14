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
  // reachable, onboarding stays completed, AND that focus lands on the heading
  // via the inert-window retry (jsdom does not enforce inert; Chromium does).
  //
  // A poll after the dialog is gone is too late: on this stack the first
  // node.focus() already lands, so deleting the retry stays green. Hold `inert`
  // on #root until the Sources heading's first focus() — that is the race the
  // retry exists for — then release so the macrotask retry can succeed.
  test("the handoff completes onboarding and lands on a usable /me/sources", async ({ page }) => {
    await loginWithSmokeCredentials(page, { dismissOnboarding: false });
    await expectOnboardingModalVisible(page);

    const dialog = page.getByRole("dialog");
    await dialog.getByRole("button", { name: /^next$/i }).click({ force: true });
    await dialog.getByRole("button", { name: /^next$/i }).click({ force: true });
    const handoff = dialog.getByRole("button", { name: /Set up documents now/ });
    await expect(handoff).toBeVisible();

    await page.evaluate(() => {
      type Attempt = { landed: boolean; inert: boolean; text: string | null };
      const w = window as unknown as { __h1Focus?: Attempt[] };
      w.__h1Focus = [];
      const root = document.getElementById("root");
      if (!root) throw new Error("#root missing");
      const origRemove = root.removeAttribute.bind(root);
      let sourcesH1Tried = false;
      root.removeAttribute = (name: string) => {
        if ((name === "inert" || name === "aria-hidden") && !sourcesH1Tried) return;
        origRemove(name);
      };
      const origFocus = HTMLElement.prototype.focus;
      HTMLElement.prototype.focus = function (
        this: HTMLElement,
        ...args: Parameters<HTMLElement["focus"]>
      ) {
        const isSourcesH1 = this.tagName === "H1" && this.textContent?.trim() === "Sources";
        if (isSourcesH1) sourcesH1Tried = true;
        origFocus.apply(this, args);
        if (!isSourcesH1) return;
        const attempt: Attempt = {
          landed: document.activeElement === this,
          inert: root.hasAttribute("inert"),
          text: this.textContent?.trim() ?? null,
        };
        w.__h1Focus!.push(attempt);
        if (attempt.inert) {
          origRemove("inert");
          origRemove("aria-hidden");
        }
      };
    });

    await handoff.click({ force: true });
    await expect(page).toHaveURL(/\/me\/sources$/, { timeout: 60_000 });

    await expect
      .poll(
        async () => {
          const attempts = await page.evaluate(() => {
            const w = window as unknown as {
              __h1Focus?: Array<{ landed: boolean; inert: boolean; text: string | null }>;
            };
            return w.__h1Focus ?? [];
          });
          return attempts.some((a) => a.text === "Sources" && a.inert && !a.landed);
        },
        { timeout: 15_000 },
      )
      .toBe(true);

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
    ).toBe("Sources");
    expect(
      await page.evaluate(() => {
        const w = window as unknown as {
          __h1Focus?: Array<{ landed: boolean; inert: boolean; text: string | null }>;
        };
        return (w.__h1Focus ?? []).some((a) => a.text === "Sources" && a.landed);
      }),
    ).toBe(true);

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
