// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Thomas Kohlborn, trading as Lumogis

/** WDIO E2E entry — bootstrap (plugin + reboot stub) before the app graph imports. */
import { setRebootRunner } from "./e2e-wdio-bootstrap";
import { createOverlayApp } from "./app";

let appInstance: ReturnType<typeof createOverlayApp> | null = null;
let e2eInitialBootDone = false;

async function runBoot(): Promise<void> {
  if (!appInstance) {
    appInstance = createOverlayApp();
  }
  if (!e2eInitialBootDone) {
    await appInstance.boot();
    e2eInitialBootDone = true;
    return;
  }
  await appInstance.rebootForE2e();
}

setRebootRunner(runBoot);
void runBoot();
