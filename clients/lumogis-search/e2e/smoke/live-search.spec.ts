// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Thomas Kohlborn, trading as Lumogis

import { browser, expect } from "@wdio/globals";
import { bootOverlay } from "../helpers/bootOverlay.js";

const coreUrl = process.env.OVERLAY_E2E_CORE_URL ?? "http://127.0.0.1:8000";
const smokeEmail = process.env.OVERLAY_E2E_SMOKE_EMAIL ?? "";
const smokePassword = process.env.OVERLAY_E2E_SMOKE_PASSWORD ?? "";

const smokeEnabled = Boolean(smokeEmail && smokePassword);

(smokeEnabled ? describe : describe.skip)("overlay smoke — live Core", () => {
  it("logs in via overlay UI and searches without error UI", async () => {
    await bootOverlay({ mockInvoke: false });

    // Point overlay at live RC stack (persisted via settings panel is out of scope — use env default URL).
    void coreUrl;

    await browser.waitUntil(async () => (await $("#login-panel").isDisplayed()) || (await $("#q").isDisplayed()), {
      timeout: 10_000,
    });

    if (await $("#login-panel").isDisplayed()) {
      await $("#login-email").setValue(smokeEmail);
      await $("#login-password").setValue(smokePassword);
      await $("#btn-login").click();
      await browser.waitUntil(async () => !(await $("#login-panel").isDisplayed()), {
        timeout: 15_000,
        timeoutMsg: "live login should hide #login-panel",
      });
    }

    expect(await $("#login-panel").isDisplayed()).toBe(false);

    const q = await $("#q");
    await q.waitForEnabled({ timeout: 5_000 });
    await q.setValue("health");

    await browser.waitUntil(
      async () => {
        const err = await $("#error");
        if (await err.isDisplayed()) return false;
        const results = await $("#results");
        return await results.isDisplayed();
      },
      { timeout: 15_000, timeoutMsg: "search should complete without #error banner" },
    );

    const err = await $("#error");
    expect(await err.isDisplayed()).toBe(false);
    expect(await $("#login-panel").isDisplayed()).toBe(false);
  });
});
