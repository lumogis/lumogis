// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-65 — forgot-password recovery surface (desktop + mobile projects).
//
// These run against a live stack without smoke credentials on purpose: the
// whole point of the pages under test is that they work for someone who is NOT
// signed in. The disabled path is the one a default deployment actually has,
// since email reset stays off until an admin configures SMTP and passes a Send
// test — so it is the path worth guarding against regression here.
//
// The enabled path (challenge → widget → request → mail → redeem) needs a
// captured SMTP relay and is covered by the orchestrator suite end to end
// (tests/test_password_reset_routes.py), which drives the real routes with the
// real ALTCHA library rather than a mocked one.

import { test, expect } from "@playwright/test";
import type { Page } from "@playwright/test";

/**
 * Pixels by which page content exceeds the viewport width, or <= 0 if it fits.
 *
 * Measured on `document.body`, deliberately. The obvious
 * `documentElement.scrollWidth - clientWidth` reads **0 even when content
 * overflows by thousands of pixels**, because the app sets `overflow-x: hidden`
 * higher up: the overflow is clipped, not prevented, so the page does not
 * scroll and `documentElement` reports nothing wrong while the text is cut off
 * at the edge. Verified by injecting a 400-character unwrappable string and
 * confirming this measure moves (0 → 3441) where the documentElement one does
 * not — without that check these assertions would have passed unconditionally.
 */
async function horizontalOverflowPx(page: Page): Promise<number> {
  return page.evaluate(() => document.body.scrollWidth - window.innerWidth);
}


test.describe("Lumogis Web forgot password (LUM-65)", () => {
  test("login page offers a recovery path", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
    await expect(page.getByRole("link", { name: /forgot password/i })).toBeVisible();
  });

  test("forgot-password page renders for an anonymous visitor", async ({ page }) => {
    // Guards the routing decision: mounted under <RequireAuth> this would
    // render the login form instead, and the regression would be invisible to
    // anyone already signed in.
    await page.goto("/forgot-password");
    await expect(page.getByRole("heading", { name: /reset your password/i })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Sign in" })).toHaveCount(0);
  });

  test("with email reset off, the page gives the admin and CLI routes", async ({ page }) => {
    await page.goto("/forgot-password");
    const disabled = page.getByTestId("forgot-password-disabled");
    const form = page.getByTestId("forgot-password-form");

    // Wait for the page to settle before branching. It renders a "Checking…"
    // state while `GET /password-reset/status` is in flight, and that state
    // carries neither testid — so sampling `disabled.count()` straight after
    // `goto` reads 0 for the loading page, takes the enabled branch, and then
    // waits out the timeout for a form that was never going to appear. The
    // first execution of this spec failed exactly that way: the assertion was
    // racing the fetch, not testing the product.
    await expect(disabled.or(form)).toBeVisible();

    // Either state is legitimate depending on how the target stack is
    // configured; assert the copy of whichever one is live rather than
    // pinning the environment.
    if (await disabled.count()) {
      await expect(disabled).toContainText(/ask an admin/i);
      await expect(disabled).toContainText("python -m scripts.reset_password");
    } else {
      await expect(form).toBeVisible();
      await expect(page.getByLabel(/email/i)).toBeVisible();
    }
  });

  test("reset page rejects a link with no token", async ({ page }) => {
    await page.goto("/reset-password");
    await expect(page.getByTestId("reset-password-no-token")).toBeVisible();
  });

  test("reset page strips the token from the URL", async ({ page }) => {
    await page.goto("/reset-password?token=prst_notarealtoken");
    await expect(page.getByTestId("reset-password-form")).toBeVisible();
    // A live token must not survive in history, a bookmark, or a screenshot.
    await expect.poll(() => new URL(page.url()).search).toBe("");
  });
});

test.describe("Lumogis Web forgot password — mobile (LUM-65)", () => {
  test.use({ viewport: { width: 390, height: 844 } });

  // No smoke credentials here, deliberately, for the same reason as the desktop
  // block: everyone reaching these pages is signed out. That also makes this
  // block runnable in the default `chromium` project rather than the
  // credentialed smoke lane.

  test("the recovery page reads on a phone without horizontal overflow", async ({
    page,
  }) => {
    // Being locked out is a phone-first situation — you reach for the device
    // that is not the one you cannot sign in on. The disabled guidance contains
    // `python -m scripts.reset_password`, an unbreakable token wider than a
    // phone's text column, so this is the realistic overflow case.
    await page.goto("/forgot-password");
    await expect(
      page.getByTestId("forgot-password-disabled").or(page.getByTestId("forgot-password-form")),
    ).toBeVisible();

    expect(
      await horizontalOverflowPx(page),
      "content is wider than a 390px viewport",
    ).toBeLessThanOrEqual(0);
  });

  test("the reset form is usable on a phone", async ({ page }) => {
    await page.goto("/reset-password?token=prst_notarealtoken");
    await expect(page.getByTestId("reset-password-form")).toBeVisible();

    expect(
      await horizontalOverflowPx(page),
      "content is wider than a 390px viewport",
    ).toBeLessThanOrEqual(0);
  });
});
