// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Playwright config for RECORDING the launch demo (LUM-181), not for CI asserts.
// Produces paced, jitter-free .webm videos of the two-user household-KB flow
// (admin shares a doc → member searches + asks). Convert to GIF with
// `scripts/demo-to-gif.sh`.
//
// Run against a LIVE stack (real ingest/share/search/chat — no route mocks):
//   docker compose up -d
//   export PLAYWRIGHT_BASE_URL=http://127.0.0.1
//   export LUMOGIS_WEB_SMOKE_EMAIL=admin@yourhome.lan  LUMOGIS_WEB_SMOKE_PASSWORD=…   # admin
//   export DEMO_MEMBER_EMAIL=partner@yourhome.lan       DEMO_MEMBER_PASSWORD=…         # member
//   npx playwright test -c playwright.demo.config.ts
//   ./scripts/demo-to-gif.sh test-results/demo/  docs/assets/demo.gif

import { defineConfig, devices } from "@playwright/test";

const BASE_URL = process.env.PLAYWRIGHT_BASE_URL ?? "http://127.0.0.1";

// Deliberate slowness: browser-level slowMo + in-spec beats make the run
// watchable. Tune DEMO_SLOWMO_MS if the GIF feels too fast/slow.
const SLOWMO = Number(process.env.DEMO_SLOWMO_MS ?? 350);

export default defineConfig({
  testDir: "./tests/e2e/demo",
  outputDir: "./test-results/demo",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  // A generous per-step timeout — the demo waits on real ingest/embed.
  timeout: 180_000,
  reporter: [["list"]],
  use: {
    baseURL: BASE_URL,
    // The whole point: capture video of every context, at a clean fixed size.
    video: { mode: "on", size: { width: 1280, height: 800 } },
    viewport: { width: 1280, height: 800 },
    // A visible, larger cursor reads better in a recording.
    launchOptions: { slowMo: SLOWMO },
    // Real backend — no offline artifacts.
    trace: "off",
    screenshot: "off",
  },
  projects: [
    {
      name: "demo-chromium",
      use: { ...devices["Desktop Chrome"], viewport: { width: 1280, height: 800 } },
    },
  ],
});
