// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Playwright config — parent plan Phase 1 Pass 1.5 step 17.
// Default baseURL is http://127.0.0.1 (Caddy on port 80). Override with
// PLAYWRIGHT_BASE_URL when testing another host.

import { defineConfig, devices } from "@playwright/test";

const baseURL = process.env.PLAYWRIGHT_BASE_URL ?? "http://127.0.0.1";
const proveMode = process.env.E2E_REQUIRE_CREDS === "1";

export default defineConfig({
  testDir: "./tests/e2e",
  fullyParallel: !proveMode,
  workers: proveMode ? 1 : undefined,
  timeout: proveMode ? 120_000 : 30_000,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: [["list"]],
  use: {
    ...devices["Desktop Chrome"],
    baseURL,
    trace: "on-first-retry",
    ...(proveMode ? { contextOptions: { reducedMotion: "reduce" as const } } : {}),
  },
  // Family-LAN auth uses a single active refresh-token jti per user; parallel
  // browser logins as the same smoke user revoke each other. Run those specs
  // in one worker (fullyParallel: false on this project only).
  projects: [
    {
      name: "chromium",
      testIgnore: [
        "**/demo/**",
        "**/me_admin_mobile_shell.spec.ts",
        "**/admin_users_mobile.spec.ts",
        "**/phase_2b_mobile_surfaces.spec.ts",
        "**/phase_2c_mobile_dense.spec.ts",
        "**/phase_2d_mobile_documents.spec.ts",
        "**/onboarding_dismiss_persists.spec.ts",
        "**/admin_ollama_mutations.spec.ts",
        "**/chat-degradation.spec.ts",
        "**/chat-sidebar-mobile.spec.ts",
      ],
    },
    {
      name: "chromium-smoke-shared-user",
      testMatch: [
        "**/me_admin_mobile_shell.spec.ts",
        "**/admin_users_mobile.spec.ts",
        "**/phase_2b_mobile_surfaces.spec.ts",
        "**/phase_2c_mobile_dense.spec.ts",
        "**/phase_2d_mobile_documents.spec.ts",
        "**/onboarding_dismiss_persists.spec.ts",
        "**/admin_ollama_mutations.spec.ts",
        "**/chat-degradation.spec.ts",
        "**/chat-sidebar-mobile.spec.ts",
      ],
      fullyParallel: false,
      workers: 1,
    },
  ],
});
