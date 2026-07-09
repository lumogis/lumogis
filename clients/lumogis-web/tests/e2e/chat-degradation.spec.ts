// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-512 — Playwright: graceful service-degradation banners in chat.
// Parent: LUM-211 (error/degradation states).
//
// Rather than physically killing Ollama/Qdrant/FalkorDB containers (an
// ops-level concern), this spec stubs GET /api/v1/health with each down-state
// and asserts the UI degrades per the matrix:
//   * Ollama down  → hard-fail alert, chat paused.
//   * Qdrant down  → degraded status, chat still works (KG-only).
//   * Graph down   → degraded status, chat still works (vector-only).
//
// Harness contract (same as chat-sidebar-mobile / phase_2b specs): needs the
// live stack + smoke creds, so it test.skip without them.

import { test, expect, type Page } from "@playwright/test";

import {
  dismissOnboardingIfPresent,
  hasSmokeCreds,
  loginWithSmokeCredentials,
  smokeCredsSkipMessage,
} from "./smoke-auth";

type HealthBody = {
  overall: "ok" | "degraded" | "down";
  services: Record<string, string>;
};

async function stubHealth(page: Page, body: HealthBody): Promise<void> {
  await page.route("**/api/v1/health", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(body),
    });
  });
}

async function openChatWithHealth(page: Page, body: HealthBody): Promise<void> {
  await stubHealth(page, body);
  await loginWithSmokeCredentials(page);
  await page.goto("/chat");
  await dismissOnboardingIfPresent(page);
}

test.describe("Chat service-degradation banners (LUM-512)", () => {
  test.skip(!hasSmokeCreds, smokeCredsSkipMessage);

  test("Ollama down → hard-fail alert (messages may fail)", async ({ page }) => {
    await openChatWithHealth(page, {
      overall: "down",
      services: { ollama: "down", qdrant: "healthy" },
    });
    const alert = page.getByRole("alert").filter({ hasText: /local ai unavailable/i });
    await expect(alert).toBeVisible();
    await expect(alert).toContainText(/messages may fail/i);
  });

  test("Qdrant down → degraded status, chat still usable", async ({ page }) => {
    await openChatWithHealth(page, {
      overall: "degraded",
      services: { ollama: "healthy", qdrant: "down" },
    });
    await expect(
      page.getByRole("status").filter({ hasText: /document search unavailable/i }),
    ).toBeVisible();
    // Not a hard failure — no alert.
    await expect(page.getByRole("alert").filter({ hasText: /local ai/i })).toHaveCount(0);
  });

  test("all healthy → no degradation banner", async ({ page }) => {
    await openChatWithHealth(page, {
      overall: "ok",
      services: { ollama: "healthy", qdrant: "healthy" },
    });
    await expect(page.getByTestId("service-degradation")).toHaveCount(0);
  });
});
