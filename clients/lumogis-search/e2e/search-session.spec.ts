// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Thomas Kohlborn, trading as Lumogis

import { browser, expect } from "@wdio/globals";
import { bootLoggedInAdmin } from "./helpers/bootOverlay.js";
import { mockInvokeReject, mockInvokeReturn } from "./helpers/mockInvoke.js";
import { sampleSearchHit } from "./mocks/invokeFixtures.js";

describe("overlay search session", () => {
  it("renders search hits when logged in", async () => {
    await bootLoggedInAdmin();
    await mockInvokeReturn("search_memory", sampleSearchHit("lumogis health hit"));

    const q = await $("#q");
    await q.setValue("health");
    await browser.waitUntil(
      async () => (await $("#results").getText()).includes("lumogis health hit"),
      { timeout: 8_000, timeoutMsg: "#results should contain mocked hit snippet" },
    );
    expect(await $("#login-panel").isDisplayed()).toBe(false);
  });

  it("returns to login when search_memory throws session_expired", async () => {
    await bootLoggedInAdmin();
    await mockInvokeReject("search_memory", "session_expired");

    const q = await $("#q");
    await q.setValue("health");
    await browser.waitUntil(async () => await $("#login-panel").isDisplayed(), {
      timeout: 8_000,
      timeoutMsg: "session_expired should show #login-panel",
    });
    expect(await q.isEnabled()).toBe(false);
  });
});
