// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Thomas Kohlborn, trading as Lumogis

import { browser, expect } from "@wdio/globals";
import { bootLoggedInAdmin } from "./helpers/bootOverlay.js";
import { mockInvokeReturn } from "./helpers/mockInvoke.js";
import { queuedUpload } from "./mocks/invokeFixtures.js";

describe("overlay ingest upload", () => {
  it("shows queued confirmation after file upload", async () => {
    await bootLoggedInAdmin();
    await mockInvokeReturn("upload_ingest_file", queuedUpload());

    await $("#btn-settings").click();
    await $("#settings").waitForDisplayed({ timeout: 5_000 });

    const input = await $("#ingest-upload-input");
    await input.setValue("/tmp/lumogis-e2e-fixture.txt");

    const hint = await $("#upload-hint");
    await browser.waitUntil(async () => /Queued \(queued\)/.test(await hint.getText()), {
      timeout: 8_000,
      timeoutMsg: "#upload-hint should show Queued (queued)",
    });
    expect(await hint.getText()).toMatch(/Queued \(queued\)/);
  });
});
