// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Playwright config — parent plan Phase 1 Pass 1.5 step 17.
// Default baseURL is http://127.0.0.1 (Caddy on port 80). Override with
// PLAYWRIGHT_BASE_URL when testing another host.

import { defineConfig, devices } from "@playwright/test";

const baseURL = process.env.PLAYWRIGHT_BASE_URL ?? "http://127.0.0.1";

export default defineConfig({
  testDir: "./tests/e2e",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: [["list"]],
  use: {
    ...devices["Desktop Chrome"],
    baseURL,
    trace: "on-first-retry",
  },
  // Family-LAN auth uses a single active refresh-token jti per user; parallel
  // browser logins as the same smoke user revoke each other. Run those specs
  // in one worker (fullyParallel: false on this project only).
  projects: [
    {
      name: "chromium",
      testIgnore: [
        "**/me_admin_mobile_shell.spec.ts",
        "**/phase_2b_mobile_surfaces.spec.ts",
        "**/phase_2c_mobile_dense.spec.ts",
        "**/public_rc_smoke.spec.ts",
        "**/public_rc_routes.spec.ts",
        "**/public_rc_nav_clickthrough.spec.ts",
        "**/public_rc_chat_workflow.spec.ts",
        "**/public_rc_capture_workflow.spec.ts",
        "**/public_rc_approvals_workflow.spec.ts",
        "**/public_rc_signed_in_routes.spec.ts",
        "**/public_rc_negative_workflows.spec.ts",
      ],
    },
    {
      name: "chromium-smoke-shared-user",
      testMatch: [
        "**/me_admin_mobile_shell.spec.ts",
        "**/phase_2b_mobile_surfaces.spec.ts",
        "**/phase_2c_mobile_dense.spec.ts",
      ],
      fullyParallel: false,
      workers: 1,
    },
    {
      name: "rc-public-gate",
      testMatch: ["**/public_rc_smoke.spec.ts", "**/public_rc_routes.spec.ts"],
      fullyParallel: true,
      workers: process.env.CI ? 4 : 6,
    },
    {
      name: "rc-public-workflows",
      testMatch: [
        "**/public_rc_chat_workflow.spec.ts",
        "**/public_rc_capture_workflow.spec.ts",
        "**/public_rc_approvals_workflow.spec.ts",
        "**/public_rc_signed_in_routes.spec.ts",
        "**/public_rc_negative_workflows.spec.ts",
      ],
      fullyParallel: false,
      workers: 1,
      dependencies: ["rc-public-gate"],
    },
    {
      name: "rc-public-auth-nav",
      testMatch: ["**/public_rc_nav_clickthrough.spec.ts"],
      fullyParallel: false,
      workers: 1,
      dependencies: ["rc-public-workflows"],
    },
  ],
});
