// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-981 — signing in at a LAN address over plain HTTP (the phone case).
//
// Before the fix, a phone opening http://<lan-address> signed in once and lost the session on
// reload (the `Secure` refresh cookie was dropped on plain HTTP) and Sign out failed with a 403
// (the compose origin pin did not match the address in the browser). This spec is the proof for
// both: it signs in, reloads, and signs out, and fails on a sign-in form after the reload or a
// 403 on the way out.
//
// Chromium treats only `localhost`, `127.0.0.0/8` and `::1` as secure contexts on plain HTTP, so
// http://localhost would hide the defect. When LUMOGIS_E2E_LAN_MAP_TO is set (normally
// 127.0.0.1), the browser is launched with `--host-resolver-rules=MAP <host> <ip>`: a name such as
// `lumogis-lan.test` then reaches the local stack and is NOT a secure context.
//
//   LUMOGIS_E2E_LAN_BASE_URL=http://lumogis-lan.test:8090 LUMOGIS_E2E_LAN_MAP_TO=127.0.0.1 \
//   LUMOGIS_WEB_SMOKE_EMAIL=... LUMOGIS_WEB_SMOKE_PASSWORD=... \
//     npx playwright test tests/e2e/lan_signin.spec.ts
//
// Skipped unless LUMOGIS_E2E_LAN_BASE_URL and the smoke credentials are set.

import { test, expect } from "@playwright/test";

import { hasSmokeCreds, loginWithSmokeCredentials, smokeCredsSkipMessage } from "./smoke-auth";

const lanBaseUrl = process.env.LUMOGIS_E2E_LAN_BASE_URL ?? "";
const mapTo = process.env.LUMOGIS_E2E_LAN_MAP_TO ?? "";

test.skip(!lanBaseUrl, "Set LUMOGIS_E2E_LAN_BASE_URL (for example http://lumogis-lan.test:8090).");
test.skip(!hasSmokeCreds, smokeCredsSkipMessage);

const lanHost = lanBaseUrl ? new URL(lanBaseUrl).hostname : "";

test.use({
  baseURL: lanBaseUrl || undefined,
  launchOptions: {
    args: mapTo && lanHost ? [`--host-resolver-rules=MAP ${lanHost} ${mapTo}`] : [],
  },
});

test.describe("LUM-981 sign-in at a LAN address", () => {
  test("sign in, survive a reload, and sign out with no 403", async ({ page }) => {
    await loginWithSmokeCredentials(page);

    await page.reload();
    await expect(page).toHaveURL(/\/chat$/, { timeout: 60_000 });
    await expect(page.getByTestId("lumogis-shell")).toBeVisible({ timeout: 60_000 });
    await expect(page.getByRole("heading", { name: "Sign in" })).toHaveCount(0);

    const forbidden: string[] = [];
    page.on("response", (res) => {
      if (res.status() === 403) forbidden.push(`${res.request().method()} ${res.url()}`);
    });

    await page.getByRole("button", { name: "Account menu" }).click();
    await page.getByRole("menuitem", { name: "Sign out" }).click();

    await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible({ timeout: 30_000 });
    expect(forbidden).toEqual([]);
  });
});
