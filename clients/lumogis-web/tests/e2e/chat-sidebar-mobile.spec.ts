// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-420 — Playwright mobile smoke for the collapsible conversation sidebar.
// Parent: LUM-162 (conversation history UI). The /chat layout is a CSS grid
// (.lumogis-chat) that collapses from `260px 1fr` (>=720px container) to a
// single `1fr` column on a narrow viewport, so the conversation sidebar stacks
// ABOVE the chat transcript instead of sitting beside it. This smoke pins that
// responsive behaviour at a phone viewport (and the side-by-side layout at
// desktop width as a counter-check).
//
// Harness contract (same as chat-conversation-history / phase_2b specs):
//   * The Lumogis stack must be running (Caddy + lumogis-web + orchestrator +
//     Postgres) so the SPA shell + real auth flow work. There is no Playwright
//     `webServer`, so this test test.skip without smoke creds + a live stack.
//   * Credentials (family-LAN smoke user):
//       export LUMOGIS_WEB_SMOKE_EMAIL=...
//       export LUMOGIS_WEB_SMOKE_PASSWORD='...'   # >= 12 chars

import { test, expect, type Page } from "@playwright/test";

import {
  dismissOnboardingIfPresent,
  hasSmokeCreds,
  loginWithSmokeCredentials,
  smokeCredsSkipMessage,
} from "./smoke-auth";

async function expectNoPageHorizontalOverflow(page: Page): Promise<void> {
  const { scrollW, clientW } = await page.evaluate(() => ({
    scrollW: document.documentElement.scrollWidth,
    clientW: document.documentElement.clientWidth,
  }));
  expect(scrollW).toBeLessThanOrEqual(clientW + 1);
}

test.describe("Chat conversation sidebar — responsive collapse", () => {
  test.skip(!hasSmokeCreds, smokeCredsSkipMessage);

  test("mobile (390x844): sidebar stacks above the transcript, both reachable", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await loginWithSmokeCredentials(page);
    await page.goto("/chat");
    await dismissOnboardingIfPresent(page);

    const sidebar = page.getByRole("complementary", { name: /conversations/i });
    const main = page.getByRole("region", { name: /^chat$/i });
    await expect(sidebar).toBeVisible();
    await expect(main).toBeVisible();

    const sb = await sidebar.boundingBox();
    const mb = await main.boundingBox();
    expect(sb).not.toBeNull();
    expect(mb).not.toBeNull();
    // Single-column (stacked): the sidebar's bottom is at/above the chat's top.
    expect(sb!.y + sb!.height).toBeLessThanOrEqual(mb!.y + 2);

    await expectNoPageHorizontalOverflow(page);
  });

  test("desktop (1280x900): sidebar sits beside the transcript", async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 900 });
    await loginWithSmokeCredentials(page);
    await page.goto("/chat");
    await dismissOnboardingIfPresent(page);

    const sidebar = page.getByRole("complementary", { name: /conversations/i });
    const main = page.getByRole("region", { name: /^chat$/i });
    await expect(sidebar).toBeVisible();
    await expect(main).toBeVisible();

    const sb = await sidebar.boundingBox();
    const mb = await main.boundingBox();
    expect(sb).not.toBeNull();
    expect(mb).not.toBeNull();
    // Two-column: sidebar is left of the transcript and they share a row.
    expect(sb!.x + sb!.width).toBeLessThanOrEqual(mb!.x + 2);
    expect(Math.abs(sb!.y - mb!.y)).toBeLessThanOrEqual(8);
  });
});
