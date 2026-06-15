// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Thomas Kohlborn, trading as Lumogis

import { browser, expect } from "@wdio/globals";
import { bootOverlay } from "./helpers/bootOverlay.js";
import { mockInvokeReject, mockInvokeReturn } from "./helpers/mockInvoke.js";
import { loggedOutProbe, loggedOutSettings, validLoginSession } from "./mocks/invokeFixtures.js";

describe("overlay login", () => {
  it("shows main search shell after valid credentials", async () => {
    await bootOverlay({
      settings: loggedOutSettings(),
      probe: loggedOutProbe(),
      adminSettings: null,
    });

    await mockInvokeReturn("auth_login", validLoginSession());

    await $("#login-email").setValue("admin@example.com");
    await $("#login-password").setValue("validpass1234");
    await $("#btn-login").click();

    await browser.waitUntil(async () => !(await $("#login-panel").isDisplayed()), {
      timeout: 8_000,
      timeoutMsg: "login panel should hide after successful auth_login mock",
    });
    const q = await $("#q");
    expect(await q.isDisplayed()).toBe(true);
    expect(await q.isEnabled()).toBe(true);
  });

  it("shows invalid credentials error", async () => {
    await bootOverlay({
      settings: loggedOutSettings(),
      probe: loggedOutProbe(),
      adminSettings: null,
    });

    await mockInvokeReject("auth_login", "invalid_credentials");

    await $("#login-email").setValue("admin@example.com");
    await $("#login-password").setValue("validpass1234");
    await $("#btn-login").click();

    const err = await $("#login-error");
    await err.waitForDisplayed({ timeout: 8_000 });
    expect(await err.getText()).toBe("Invalid email or password.");
  });
});
