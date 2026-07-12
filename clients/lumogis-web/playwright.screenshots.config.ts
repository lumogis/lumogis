// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Capture labelled PNG screenshots of main Lumogis Web screens (manual / design review).
//   make web-screenshots

import { defineConfig, devices } from "@playwright/test";

const BASE_URL = process.env.PLAYWRIGHT_BASE_URL ?? "http://127.0.0.1";

export default defineConfig({
  testDir: "./tests/e2e/screenshots",
  outputDir: "./test-results/screenshots",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 120_000,
  reporter: [["list"]],
  use: {
    baseURL: BASE_URL,
    viewport: { width: 1280, height: 800 },
    ...devices["Desktop Chrome"],
    trace: "off",
    video: "off",
  },
  projects: [{ name: "screenshots-chromium" }],
});
