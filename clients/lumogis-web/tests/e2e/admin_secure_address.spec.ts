// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-331 — Admin → Secure address: the nav reaches the page, and the page's
// own flows (confirm the address, turn on, see the address, open the router
// guide, turn off) work on desktop and on a phone-sized screen.
//
// Hybrid: smoke login on a live stack + `page.route` mocks for
// /api/v1/admin/secure-address. The mocks drive the page through its states;
// what is asserted is the page's behaviour (confirmation gate, which request
// it sends, which controls appear), not the states themselves.
//
// Run (see `make web-e2e-prove`):
//   export LUMOGIS_WEB_SMOKE_EMAIL=...  LUMOGIS_WEB_SMOKE_PASSWORD='...'  # >= 12 chars
//   export LUMOGIS_E2E_EXPECT_ADMIN=1
//   npx playwright test tests/e2e/admin_secure_address.spec.ts --project=chromium-smoke-shared-user
//
// NOT covered here, on purpose (vacuous-test principle: do not assert
// server-computed logic through the browser):
//   - which state the server derives, its error codes and the polling stop
//     condition: pytest truth table (test_home_dns_state.py,
//     test_home_dns_service.py) and Vitest with fake timers
//     (AdminSecureAddressView.test.tsx).
//   - that the CSP lets the device check reach *.homes.lumogis.app:
//     tests/integration/test_caddy_security_headers.py.
//   - the name-new window and which state it yields: pytest
//     (test_home_dns_state.py); only the copy it switches on is Vitest.
//   - the start-up re-apply after a Core upgrade or CA switch: pytest.
//   - a real certificate and a real router: Pass 8 live proof (LUM-523).

import { test, expect, type Page } from "@playwright/test";

import { hasSmokeCreds, loginWithSmokeCredentials, smokeCredsSkipMessage } from "./smoke-auth";

const expectAdmin =
  process.env.LUMOGIS_E2E_EXPECT_ADMIN === "1"
    ? ""
    : "Set LUMOGIS_E2E_EXPECT_ADMIN=1 (admin smoke user required).";

const NAME = "kq7f3r2abcdefghijklmnopqrs.homes.lumogis.app";
const URL_ = `https://${NAME}`;

function status(over: Record<string, unknown> = {}) {
  return {
    state: "off",
    name: null,
    url: null,
    lan_address: null,
    lan_address_source: null,
    suggested_address: "192.168.1.20",
    detection_unavailable_reason: null,
    certificate_valid_until: null,
    resolver: "unknown",
    error_code: null,
    retry_after_seconds: null,
    name_changed: false,
    name_new_until: null,
    sign_in_enabled: true,
    warnings: [],
    ...over,
  };
}

const READY = status({
  state: "ready",
  name: NAME,
  url: URL_,
  lan_address: "192.168.1.20",
  lan_address_source: "detected",
  certificate_valid_until: "2027-02-14T12:00:00Z",
  resolver: "ok",
});

/** Off until enabled; enable answers `registering`, the next GET is `ready`; disable goes back to off. */
async function installMocks(
  page: Page,
  opts: { detectFinds?: string } = {},
): Promise<{ posts: { path: string; body: unknown }[]; detects: { count: number } }> {
  const posts: { path: string; body: unknown }[] = [];
  const detects = { count: 0 };
  // With `detectFinds`, the page starts with no suggestion and /detect records one, the way the server does.
  let current: Record<string, unknown> = opts.detectFinds
    ? status({ suggested_address: null })
    : status();
  await page.route("**/api/v1/admin/secure-address**", async (route) => {
    const req = route.request();
    const path = new URL(req.url()).pathname;
    if (req.method() === "GET") {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(current) });
      if (current.state === "registering") current = READY;
      return;
    }
    posts.push({ path, body: req.postDataJSON() });
    if (path.endsWith("/enable")) {
      current = status({ state: "registering", lan_address: "192.168.1.20", lan_address_source: "manual" });
      await route.fulfill({ status: 202, contentType: "application/json", body: JSON.stringify(current) });
      return;
    }
    if (path.endsWith("/disable")) {
      current = status();
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(current) });
      return;
    }
    await route.fulfill({ status: 409, contentType: "application/json", body: '{"detail":"off"}' });
  });
  // Registered after the catch-all above, so it wins for /detect. Not recorded in `posts`.
  await page.route("**/api/v1/admin/secure-address/detect", async (route) => {
    detects.count += 1;
    if (opts.detectFinds) current = status({ suggested_address: opts.detectFinds });
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(current) });
  });
  return { posts, detects };
}

async function turnOnAndOff(page: Page): Promise<void> {
  const { posts } = await installMocks(page);
  await page.goto("/settings/home-address");
  await expect(page.getByRole("heading", { level: 1, name: "Home address" })).toBeVisible();
  await expect(page.getByRole("status").filter({ hasText: "The secure address is off." })).toBeVisible();

  const field = page.getByLabel("This computer's address on your home network");
  await expect(field).toHaveValue("192.168.1.20");
  const turnOn = page.getByRole("button", { name: "Turn on secure address" });
  await expect(turnOn).toBeDisabled();
  await page.getByRole("checkbox", { name: "Yes, my phone and laptop open Lumogis at this address." }).check();
  await turnOn.click();

  await expect(page.getByRole("status").filter({ hasText: "Your secure address is ready." })).toBeVisible({
    timeout: 15_000,
  });
  expect(posts[0]).toEqual({
    path: "/api/v1/admin/secure-address/enable",
    body: { lan_address: "192.168.1.20" },
  });
  await expect(page.getByTestId("secure-address-url")).toHaveAttribute("href", URL_);
  await expect(page.getByRole("button", { name: "Copy address" })).toBeVisible();

  await page.getByText("Allow it in your router").click();
  await expect(page.getByRole("heading", { name: "FRITZ!Box" })).toBeVisible();

  await page.getByRole("button", { name: "Turn off" }).click();
  await page.getByRole("button", { name: "Yes, turn it off" }).click();
  await expect(page.getByRole("status").filter({ hasText: "The secure address is off." })).toBeVisible();
  expect(posts.map((p) => p.path)).toEqual([
    "/api/v1/admin/secure-address/enable",
    "/api/v1/admin/secure-address/disable",
  ]);
}

async function detectsOnOpen(page: Page): Promise<void> {
  const { detects } = await installMocks(page, { detectFinds: "192.168.1.30" });
  await page.goto("/settings/home-address");
  await expect(page.getByLabel("This computer's address on your home network")).toHaveValue("192.168.1.30");
  expect(detects.count).toBe(1);

  await page.getByRole("button", { name: "Look again" }).click();
  await expect.poll(() => detects.count).toBe(2);
}

test.describe("LUM-331 admin secure address page", () => {
  test.skip(!hasSmokeCreds, smokeCredsSkipMessage);
  test.skip(!!expectAdmin, expectAdmin);

  test("desktop: the Settings menu opens the page", async ({ page }) => {
    await loginWithSmokeCredentials(page);
    await installMocks(page);
    await page.goto("/settings/members");
    await page
      .getByRole("navigation", { name: "Settings" })
      .getByRole("link", { name: "Home address" })
      .click();
    await expect(page).toHaveURL(/\/settings\/home-address$/);
    await expect(page.getByRole("heading", { level: 1, name: "Home address" })).toBeVisible();
  });

  test("desktop: confirm, turn on, see the address, turn off", async ({ page }) => {
    await loginWithSmokeCredentials(page);
    await turnOnAndOff(page);
  });

  test("desktop: opening the page while off looks for the address once and fills it in", async ({
    page,
  }) => {
    await loginWithSmokeCredentials(page);
    await detectsOnOpen(page);
  });

  test.describe("mobile (390x844)", () => {
    test.use({ viewport: { width: 390, height: 844 } });

    test("confirm, turn on, see the address, turn off", async ({ page }) => {
      await loginWithSmokeCredentials(page);
      await turnOnAndOff(page);
      const overflow = await page.evaluate(
        () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
      );
      expect(overflow).toBeLessThanOrEqual(1);
    });

    test("opening the page while off looks for the address once and fills it in", async ({ page }) => {
      await loginWithSmokeCredentials(page);
      await detectsOnOpen(page);
    });
  });
});
