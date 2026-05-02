// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Streaming chat send — relies on LUMOGIS_RC_CHAT_STUB on Core (deterministic SSE).

import { test, expect } from "@playwright/test";

import { attachRcMonitoring, assertRcHealthy } from "./helpers/rcHarness";
import { hasRcSmokeCreds, signInRcSmoke } from "./helpers/rcSignedIn";

const stubReply = "RC_CHAT_STUB_ACK";

test.beforeEach(() => {
  test.skip(!hasRcSmokeCreds, "Set LUMOGIS_WEB_SMOKE_EMAIL and LUMOGIS_WEB_SMOKE_PASSWORD (≥12 chars).");
});

test("send chat message and receive stub stream reply", async ({ page }) => {
  const mon = attachRcMonitoring(page);
  await signInRcSmoke(page);
  await page.goto("/chat", { waitUntil: "domcontentloaded" });
  await expect(page.getByTestId("chat-page")).toBeVisible({ timeout: 60_000 });

  const modelSelect = page.getByLabel("Model");
  await expect(modelSelect).toBeVisible({ timeout: 60_000 });
  await expect(modelSelect.locator('option[value="llama"]')).toBeAttached({ timeout: 120_000 });
  await modelSelect.selectOption("llama");

  const msg = `rc-chat-e2e-${Date.now()}`;
  await page.locator("#lumogis-chat-input").fill(msg);
  await page.getByRole("button", { name: /^send$/i }).click();

  await expect(page.locator(".lumogis-chat__bubble--user").filter({ hasText: msg })).toBeVisible({
    timeout: 30_000,
  });
  await expect(
    page.locator(".lumogis-chat__bubble--assistant").filter({ hasText: stubReply }),
  ).toBeVisible({ timeout: 60_000 });

  assertRcHealthy(mon);
});
