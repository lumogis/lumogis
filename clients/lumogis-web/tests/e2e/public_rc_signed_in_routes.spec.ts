// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Deep signed-in smoke for stable Me / Admin headings (desktop).

import { test, expect } from "@playwright/test";

import { attachRcMonitoring, assertRcHealthy } from "./helpers/rcHarness";
import { hasRcSmokeCreds, signInRcSmoke } from "./helpers/rcSignedIn";

const routes: { path: string; heading: RegExp }[] = [
  { path: "/me/profile", heading: /^Profile$/i },
  { path: "/me/connectors", heading: /^Connectors$/i },
  { path: "/me/permissions", heading: /^Permissions$/i },
  { path: "/me/tools-capabilities", heading: /Tools & capabilities/i },
  { path: "/me/llm-providers", heading: /^LLM providers$/i },
  { path: "/me/mcp-tokens", heading: /^MCP tokens$/i },
  { path: "/me/notifications", heading: /^Notifications$/i },
  { path: "/me/export", heading: /^Export$/i },
  { path: "/admin/diagnostics", heading: /^Diagnostics$/i },
  { path: "/admin/users", heading: /^Users$/i },
  { path: "/admin/connector-credentials", heading: /^Connector credentials$/i },
  { path: "/admin/connector-permissions", heading: /Connector permissions/i },
  { path: "/admin/mcp-tokens", heading: /MCP tokens \(admin\)/i },
  { path: "/admin/audit", heading: /^Audit$/i },
];

test.describe("RC signed-in routes — desktop", () => {
  test.beforeEach(() => {
    test.skip(!hasRcSmokeCreds, "Set LUMOGIS_WEB_SMOKE_EMAIL and LUMOGIS_WEB_SMOKE_PASSWORD (≥12 chars).");
  });

  for (const { path, heading } of routes) {
    test(`GET ${path} renders signed-in`, async ({ page }) => {
      const mon = attachRcMonitoring(page);
      await signInRcSmoke(page);
      await page.goto(path, { waitUntil: "domcontentloaded" });
      await expect(page.getByRole("heading", { name: heading })).toBeVisible({ timeout: 90_000 });
      assertRcHealthy(mon);
    });
  }
});
