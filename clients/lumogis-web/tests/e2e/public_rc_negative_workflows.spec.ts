// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Negative paths + offline UX — deterministic RC gate (after smoke workflows).

import { test, expect } from "@playwright/test";

import { attachRcMonitoring, assertRcHealthy } from "./helpers/rcHarness";
import { hasRcSmokeCreds, signInRcSmoke } from "./helpers/rcSignedIn";

test.beforeEach(() => {
  test.skip(!hasRcSmokeCreds, "Set LUMOGIS_WEB_SMOKE_EMAIL and LUMOGIS_WEB_SMOKE_PASSWORD (≥12 chars).");
});

test("chat: Send stays disabled with empty composer", async ({ page }) => {
  const mon = attachRcMonitoring(page);
  await signInRcSmoke(page);
  await page.goto("/chat", { waitUntil: "domcontentloaded" });
  await expect(page.getByTestId("chat-page")).toBeVisible({ timeout: 60_000 });

  const modelSelect = page.getByLabel("Model");
  await expect(modelSelect).toBeVisible({ timeout: 60_000 });
  await expect(modelSelect.locator('option[value="llama"]')).toBeAttached({ timeout: 120_000 });
  await modelSelect.selectOption("llama");

  await expect(page.locator("#lumogis-chat-input")).toHaveValue("");
  await expect(page.getByRole("button", { name: /^send$/i })).toBeDisabled();

  assertRcHealthy(mon);
});

test("capture: empty server save shows validation (no camera)", async ({ page }) => {
  const mon = attachRcMonitoring(page);
  await signInRcSmoke(page);
  await page.goto("/capture", { waitUntil: "domcontentloaded" });
  await expect(page.getByTestId("quick-capture-page")).toBeVisible({ timeout: 60_000 });

  await page.getByTestId("quick-capture-save-server").click();
  await expect(page.getByTestId("quick-capture-error")).toContainText(/Enter some text/i, {
    timeout: 30_000,
  });

  assertRcHealthy(mon);
});

test("offline: banner visible and chat Send disabled", async ({ page, context }) => {
  const mon = attachRcMonitoring(page);
  await signInRcSmoke(page);

  await page.goto("/chat", { waitUntil: "domcontentloaded" });
  await expect(page.getByTestId("chat-page")).toBeVisible({ timeout: 60_000 });

  const modelSelect = page.getByLabel("Model");
  await expect(modelSelect.locator('option[value="llama"]')).toBeAttached({ timeout: 120_000 });
  await modelSelect.selectOption("llama");
  await page.locator("#lumogis-chat-input").fill("offline probe");

  await context.setOffline(true);
  await expect(page.getByTestId("lumogis-offline-banner")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByRole("button", { name: /^send$/i })).toBeDisabled();

  await context.setOffline(false);
  assertRcHealthy(mon);
});
