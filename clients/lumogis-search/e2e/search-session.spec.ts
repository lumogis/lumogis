// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Thomas Kohlborn, trading as Lumogis

import { browser, expect } from "@wdio/globals";
import { bootLoggedInAdmin, closeSettingsPanelIfOpen, openSettingsPanel } from "./helpers/bootOverlay.js";
import { mockInvokeReject, mockInvokeReturn } from "./helpers/mockInvoke.js";
import { queuedUpload, sampleSearchHit } from "./mocks/invokeFixtures.js";

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

  it("shows queued confirmation after ingest file upload", async () => {
    await mockInvokeReturn("upload_ingest_file", queuedUpload());
    await openSettingsPanel();

    await browser.tauri.execute(() => {
      const input = document.querySelector<HTMLInputElement>("#ingest-upload-input");
      if (!input) {
        throw new Error("#ingest-upload-input missing");
      }
      const dt = new DataTransfer();
      dt.items.add(new File(["e2e"], "lumogis-e2e-fixture.txt", { type: "text/plain" }));
      input.files = dt.files;
      input.dispatchEvent(new Event("change", { bubbles: true }));
    });

    const hint = await $("#upload-hint");
    await browser.waitUntil(async () => /Queued \(queued\)/.test(await hint.getText()), {
      timeout: 8_000,
      timeoutMsg: "#upload-hint should show Queued (queued)",
    });
    expect(await hint.getText()).toMatch(/Queued \(queued\)/);
  });

  it("returns to login when search_memory throws session_expired", async () => {
    await mockInvokeReject("search_memory", "session_expired");
    await closeSettingsPanelIfOpen();

    const q = await $("#q");
    await q.waitForDisplayed({ timeout: 5_000 });
    await q.clearValue();
    await q.setValue("health");
    await browser.waitUntil(async () => await $("#login-panel").isDisplayed(), {
      timeout: 8_000,
      timeoutMsg: "session_expired should show #login-panel",
    });
    expect(await q.isEnabled()).toBe(false);
  });
});
