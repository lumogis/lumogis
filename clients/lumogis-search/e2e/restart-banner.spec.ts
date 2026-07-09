// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Thomas Kohlborn, trading as Lumogis

import { browser, expect } from "@wdio/globals";
import { bootLoggedInAdmin, openSettingsPanel } from "./helpers/bootOverlay.js";
import { mockInvokeImpl, mockInvokeReturn } from "./helpers/mockInvoke.js";
import { defaultAdminSettings } from "./mocks/invokeFixtures.js";

describe("overlay restart banner", () => {
  it("shows restart banner when restartRequired is true", async () => {
    await bootLoggedInAdmin();
    await mockInvokeReturn(
      "fetch_admin_settings",
      defaultAdminSettings({ restartRequired: true }),
    );

    await openSettingsPanel();
    const banner = await $("#restart-banner");
    await banner.waitForDisplayed({ timeout: 5_000 });
    expect(await banner.isDisplayed()).toBe(true);
  });

  it("shows restart requested hint after confirm + mock restart", async () => {
    await mockInvokeReturn(
      "fetch_admin_settings",
      defaultAdminSettings({ restartRequired: true }),
    );
    await mockInvokeImpl("restart_orchestrator_stack", () => undefined);

    await openSettingsPanel();

    await browser.tauri.execute(() => {
      window.confirm = () => true;
    });
    await $("#btn-restart-stack").click();

    const hint = await $("#ingest-admin-hint");
    await browser.waitUntil(async () => (await hint.getText()).includes("Restart requested"), {
      timeout: 8_000,
      timeoutMsg: "#ingest-admin-hint should mention Restart requested",
    });
    expect(await hint.getText()).toContain("Restart requested");
  });
});
