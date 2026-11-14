// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-881 — the privacy-mode explanation, end to end on a live stack:
// credential row -> `disabled_reason` -> hint -> link.
//
// Nothing here is mocked (no `page.route` for /api/v1/models or
// /me/privacy-mode): mocking them would only re-assert the fixture.
//
// Run (see `make web-e2e-prove`):
//   export LUMOGIS_WEB_SMOKE_EMAIL=...  LUMOGIS_WEB_SMOKE_PASSWORD='...'  # >= 12 chars
//   export LUMOGIS_E2E_EXPECT_ADMIN=1
//   npx playwright test tests/e2e/privacy_mode_hint.spec.ts --project=chromium-smoke-shared-user
//
// This spec OWNS the smoke admin's per-user `llm_anthropic` row. The smoke
// admin is a dedicated e2e identity (smoke-auth.ts); `beforeAll` deletes the row
// (404 is fine) so an aborted earlier run heals instead of skipping forever.
// Household and system tiers are never touched, and the household policy is
// never flipped. Serial, single worker (same project as the other shared-user
// specs).
//
// NOT covered here, on purpose:
//   - member wording, lock wording, `allow_cloud` absence: server-computed or
//     role-dependent; covered in Vitest (privacyBlockCopy.test.ts,
//     PrivacyBlockedHint.test.tsx), because the shared smoke stack must not get
//     a second household member or a flipped instance lock.
//   - an AUTH_ENABLED=false stack: the e2e lanes run auth-on; the only
//     difference is role="admin" from /auth/me, covered in Vitest.
//   - the document-chat hint: needs an indexed document on the live stack; same
//     component and condition as chat, proven in Vitest (DocumentChatPage.test.tsx).

import { test, expect, type APIRequestContext, type Page } from "@playwright/test";

import {
  hasSmokeCreds,
  loginWithSmokeCredentials,
  smokeCredsSkipMessage,
  smokeEmail,
  smokePassword,
} from "./smoke-auth";

const KEY_ENDPOINT = "/api/v1/me/connector-credentials/llm_anthropic";
const DUMMY_KEY = "sk-ant-dummy-000000000000";

const expectAdmin =
  process.env.LUMOGIS_E2E_EXPECT_ADMIN === "1"
    ? ""
    : "Set LUMOGIS_E2E_EXPECT_ADMIN=1 (admin smoke user required).";

interface Descriptor {
  id: string;
  enabled: boolean;
  disabled_reason?: string | null;
}

/** A bearer token minted with an API login (the UI keeps its token in memory). */
async function apiToken(request: APIRequestContext): Promise<string> {
  const res = await request.post("/api/v1/auth/login", {
    data: { email: smokeEmail, password: smokePassword },
  });
  expect(res.ok(), `API login failed: ${res.status()}`).toBeTruthy();
  return ((await res.json()) as { access_token: string }).access_token;
}

async function claudeReason(request: APIRequestContext, token: string): Promise<string | null | undefined> {
  const res = await request.get("/api/v1/models", { headers: { Authorization: `Bearer ${token}` } });
  expect(res.ok()).toBeTruthy();
  const models = ((await res.json()) as { models: Descriptor[] }).models;
  return models.find((m) => m.id === "claude")?.disabled_reason;
}

async function deleteKey(request: APIRequestContext, token: string): Promise<void> {
  const res = await request.delete(KEY_ENDPOINT, { headers: { Authorization: `Bearer ${token}` } });
  // 404 = nothing to delete; both leave the row absent.
  expect([200, 204, 404]).toContain(res.status());
}

async function saveKeyOnConnectorsPage(page: Page): Promise<void> {
  await page.goto("/me/connectors");
  const card = page.locator("article", {
    // The heading also carries the status chip ("llm_anthropicNot configured").
    has: page.getByRole("heading", { name: /^llm_anthropic/ }),
  });
  await expect(card).toBeVisible({ timeout: 60_000 });
  await card.getByLabel("API key").fill(DUMMY_KEY);
  await card.getByRole("button", { name: "Save" }).click();
  await expect(page.getByText("Saved.", { exact: true })).toBeVisible();
  await expect(card.getByTestId("privacy-blocked-hint")).toContainText(
    "Cloud models stay off while household privacy mode is local-only.",
  );
}

test.describe.configure({ mode: "serial" });

test.describe("LUM-881 privacy-mode hint (live)", () => {
  test.skip(!hasSmokeCreds, smokeCredsSkipMessage);
  test.skip(!!expectAdmin, expectAdmin);

  let token = "";
  let skipReason = "";

  test.beforeAll(async ({ request }) => {
    token = await apiToken(request);
    const policy = await request.get("/api/v1/me/privacy-mode", {
      headers: { Authorization: `Bearer ${token}` },
    });
    const effective = ((await policy.json()) as { instance?: { privacy_effective?: string } }).instance
      ?.privacy_effective;
    if (effective !== "local_only") {
      skipReason = `Household privacy mode is "${effective}", not local_only; this spec never flips the policy.`;
      return;
    }
    await deleteKey(request, token); // heal an aborted earlier run
    const before = await claudeReason(request, token);
    if (before !== "no_credentials") {
      skipReason = `Before-state: claude reads ${String(before)}, not no_credentials (a household/system key exists?).`;
    }
  });

  test.afterAll(async ({ request }) => {
    if (token) await deleteKey(request, token);
  });

  test.beforeEach(() => {
    test.skip(skipReason !== "", skipReason);
  });

  test("desktop: save a key -> key-save hint -> picker explains -> link goes to Privacy mode", async ({ page }) => {
    await loginWithSmokeCredentials(page);

    await saveKeyOnConnectorsPage(page);

    await page.goto("/chat");
    // A <select>'s options are not exposed as visible role=option while it is closed.
    const option = page.locator('select[aria-label="Model"] option', { hasText: /^claude/i });
    await expect(option).toHaveText(/off \(privacy mode\)/, { timeout: 60_000 });
    const hint = page.getByTestId("privacy-blocked-hint");
    await expect(hint).toBeVisible();
    await hint.getByRole("link").click();
    await expect(page).toHaveURL(/\/admin\/privacy-mode$/);
  });

  test.describe("mobile (390x844)", () => {
    test.use({ viewport: { width: 390, height: 844 } });

    test("the chat hint is visible and its link is not clipped", async ({ page, request }) => {
      // The desktop test's key is still present (serial); save it again if not.
      if ((await claudeReason(request, token)) !== "privacy_mode") {
        await loginWithSmokeCredentials(page);
        await saveKeyOnConnectorsPage(page);
      } else {
        await loginWithSmokeCredentials(page);
      }
      await page.goto("/chat");
      const hint = page.getByTestId("privacy-blocked-hint");
      await expect(hint).toBeVisible({ timeout: 60_000 });
      const box = await hint.getByRole("link").boundingBox();
      expect(box).not.toBeNull();
      const viewport = page.viewportSize()!;
      expect(box!.x).toBeGreaterThanOrEqual(0);
      expect(box!.x + box!.width).toBeLessThanOrEqual(viewport.width + 1);
    });
  });
});
