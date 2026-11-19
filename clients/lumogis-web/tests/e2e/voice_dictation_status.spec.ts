// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-995 — Playwright: dictation says what is happening, in visible text.
//
// What this proves, and what it does not. The transcribe route is STUBBED, so this
// spec proves the CLIENT: that "Listening…" and the level meter appear while
// recording, and that each error code from the server reaches the person as text on
// the page (not a tooltip). It does not prove the server's 404 -> `stt_model_missing`
// mapping or that the speech model installs: those are the backend tests and the
// recorded live-sidecar proof (Pass 2 step 17 in the plan). Asserting server-computed
// logic through a stub would be a vacuous test.
//
// Harness contract (same as chat-sidebar-mobile): needs the live stack + smoke creds,
// so it test.skips without them. Chromium's fake microphone stands in for a real one.

import { test, expect, type Page } from "@playwright/test";

import {
  dismissOnboardingIfPresent,
  hasSmokeCreds,
  loginWithSmokeCredentials,
  smokeCredsSkipMessage,
} from "./smoke-auth";

test.use({
  launchOptions: {
    args: ["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream"],
  },
  permissions: ["microphone"],
});

async function openChat(page: Page): Promise<void> {
  // Since LUM-993 the microphone shows only while /api/v1/me/shell reports a
  // speech engine; say there is one, so these tests reach the button whatever
  // the stack runs. The error paths below stub /voice/transcribe themselves.
  await page.route("**/api/v1/me/shell", async (route) => {
    const res = await route.fetch().catch(() => null);
    const real = res && res.ok() ? ((await res.json()) as Record<string, unknown>) : {};
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        email_connected: false,
        can_act: false,
        pending_approvals: 0,
        online_ai_allowed: false,
        knowledge_graph_on: false,
        ...real,
        voice_available: true,
      }),
    });
  });
  await loginWithSmokeCredentials(page);
  await page.goto("/chat");
  await dismissOnboardingIfPresent(page);
}

async function stubTranscribe(page: Page, code: string, message: string): Promise<void> {
  await page.route("**/api/v1/voice/transcribe", (route) =>
    route.fulfill({
      status: 503,
      contentType: "application/json",
      body: JSON.stringify({ detail: { code, message } }),
    }),
  );
}

function defineDictationTests(): void {
  test("shows Listening… with a level meter while recording", async ({ page }) => {
    await openChat(page);
    await page.getByRole("button", { name: "Dictate" }).click();
    await expect(page.getByRole("status").filter({ hasText: "Listening…" })).toBeVisible();
    await expect(page.getByTestId("voice-level")).toBeVisible();
    await page.getByRole("button", { name: "Stop dictation" }).click();
  });

  test("a model still downloading is shown as text, not a tooltip", async ({ page }) => {
    await stubTranscribe(page, "stt_model_missing", "installing");
    await openChat(page);
    await page.getByRole("button", { name: "Dictate" }).click();
    await expect(page.getByText("Listening…")).toBeVisible();
    await page.getByRole("button", { name: "Stop dictation" }).click();
    await expect(
      page.getByText("The speech model is still downloading. Try again in a minute."),
    ).toBeVisible();
  });

  test("speech-to-text switched off says so", async ({ page }) => {
    await stubTranscribe(page, "stt_disabled", "off");
    await openChat(page);
    await page.getByRole("button", { name: "Dictate" }).click();
    await expect(page.getByText("Listening…")).toBeVisible();
    await page.getByRole("button", { name: "Stop dictation" }).click();
    await expect(page.getByText("Speech-to-text is off on this Lumogis.")).toBeVisible();
  });
}

test.describe("Dictation status (LUM-995) — desktop", () => {
  test.skip(!hasSmokeCreds, smokeCredsSkipMessage);
  defineDictationTests();
});

test.describe("Dictation status (LUM-995) — mobile viewport", () => {
  test.skip(!hasSmokeCreds, smokeCredsSkipMessage);
  test.use({ viewport: { width: 390, height: 844 }, hasTouch: true });
  defineDictationTests();
});
