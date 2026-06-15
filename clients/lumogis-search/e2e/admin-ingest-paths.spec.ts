// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Thomas Kohlborn, trading as Lumogis

import { expect } from "@wdio/globals";
import { bootLoggedInAdmin, bootLoggedInMember } from "./helpers/bootOverlay.js";
import { mockInvokeReturn } from "./helpers/mockInvoke.js";
import { defaultAdminSettings } from "./mocks/invokeFixtures.js";

describe("overlay admin ingest paths", () => {
  it("shows ingest path editor for admin role", async () => {
    await bootLoggedInAdmin();
    await mockInvokeReturn("fetch_admin_settings", defaultAdminSettings());

    await $("#btn-settings").click();
    await $("#settings").waitForDisplayed({ timeout: 5_000 });
    const list = await $("#ingest-paths-list");
    await list.waitForDisplayed({ timeout: 5_000 });
    expect(await list.isDisplayed()).toBe(true);
  });

  it("hides ingest path editor for member role", async () => {
    await bootLoggedInMember();

    await $("#btn-settings").click();
    await $("#settings").waitForDisplayed({ timeout: 5_000 });
    const list = await $("#ingest-paths-list");
    expect(await list.isExisting()).toBe(false);
  });
});
