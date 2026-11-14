// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-886 — live two-member proof that signing out hands the next member a clean device.
//
// Why a real browser: jsdom + fake-indexeddb only approximate the ordering between a
// page's unmount-time IndexedDB draft write and the sign-out wipe (D8), and the
// cross-tab `storage` event (D13). The Vitest suites (`tests/auth/`) prove the logic;
// this proves the engine behaviour.
//
// Requires a running stack (`make web-e2e-prove`) with admin (LUMOGIS_WEB_SMOKE_*) and
// member (DEMO_MEMBER_*) credentials. Run with E2E_REQUIRE_CREDS=1 so a missing
// credential fails instead of skipping. No route mocks.

import { test, expect, type Page } from "@playwright/test";

import { adminCreds, hasTwoUserCreds, loginAs, memberCreds, twoUserCredsSkipMessage } from "./two-user-auth";

test.describe.configure({ mode: "serial", timeout: 240_000 });

const THREADS_KEY = "lumogis-chat-threads";
const MARKER_KEY = "lumogis:last-signed-in-user";

async function signOut(page: Page): Promise<void> {
  await page.getByRole("button", { name: "Account menu" }).click();
  await page.getByRole("menuitem", { name: "Sign out" }).click();
  await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible({ timeout: 15_000 });
}

/** Every key of idb-keyval's default store (`keyval-store`/`keyval`), as strings. */
async function idbKeys(page: Page): Promise<string[]> {
  return page.evaluate(
    () =>
      new Promise<string[]>((resolve, reject) => {
        const open = indexedDB.open("keyval-store");
        open.onerror = () => reject(open.error);
        open.onsuccess = () => {
          const db = open.result;
          if (!db.objectStoreNames.contains("keyval")) {
            db.close();
            resolve([]);
            return;
          }
          const req = db.transaction("keyval", "readonly").objectStore("keyval").getAllKeys();
          req.onerror = () => reject(req.error);
          req.onsuccess = () => {
            db.close();
            resolve(req.result.filter((k): k is string => typeof k === "string"));
          };
        };
      }),
  );
}

async function threadIds(page: Page): Promise<string[]> {
  return page.evaluate((key) => {
    const raw = sessionStorage.getItem(key);
    if (!raw) return [];
    try {
      const parsed = JSON.parse(raw) as { threads?: Array<{ id?: string }> };
      return (parsed.threads ?? []).map((t) => t.id ?? "").filter(Boolean);
    } catch {
      return [];
    }
  }, THREADS_KEY);
}

test.describe("LUM-886 sign-out client state (live)", () => {
  test.skip(!hasTwoUserCreds, twoUserCredsSkipMessage);

  test("54 signing out hands the next member a clean device", async ({ page, context }) => {
    let storedOutboxRow = false;
    try {
      await loginAs(page, adminCreds.email, adminCreds.password);
      await expect(page.getByTestId("chat-page")).toBeVisible({ timeout: 30_000 });
      const markerA = await page.evaluate((k) => localStorage.getItem(k), MARKER_KEY);
      expect(markerA).toBeTruthy();
      const adminThreadIds = await threadIds(page);

      // A quick-capture draft and an unsynced offline capture (the only copy — must survive).
      await page.goto("/capture");
      const note = page.getByPlaceholder(/short note/i);
      await note.fill("admin offline capture");
      await context.setOffline(true);
      await page.waitForFunction(() => navigator.onLine === false);
      await page.getByTestId("quick-capture-save-local").click();
      storedOutboxRow = true;
      await context.setOffline(false);
      await page.waitForFunction(() => navigator.onLine === true);
      await note.fill("admin quick-capture draft");

      // Type in the chat composer and sign out inside the 400 ms debounce, so the only draft
      // write is ChatPage's unmount write racing the wipe (D8 in a real browser).
      await page.goto("/chat");
      await expect(page.getByTestId("chat-page")).toBeVisible();
      await page.getByPlaceholder("Ask Lumogis…").fill("admin half-typed secret");
      await signOut(page);

      // --- on the login form, before the next sign-in
      expect(await page.evaluate((k) => sessionStorage.getItem(k), THREADS_KEY)).toBeNull();
      const keys = await idbKeys(page);
      expect(keys.filter((k) => k.startsWith("lumogis:draft:"))).toEqual([]);
      expect(keys.some((k) => k.startsWith("lumogis:capture-outbox:"))).toBe(true);

      // --- the member signs in
      await loginAs(page, memberCreds.email, memberCreds.password);
      await expect(page.getByTestId("chat-page")).toBeVisible({ timeout: 30_000 });
      await expect(page.getByPlaceholder("Ask Lumogis…")).toHaveValue("");
      const memberThreads = await threadIds(page);
      for (const id of adminThreadIds) expect(memberThreads).not.toContain(id);
      const markerB = await page.evaluate((k) => localStorage.getItem(k), MARKER_KEY);
      expect(markerB).toBeTruthy();
      expect(markerB).not.toBe(markerA);

      await page.goto("/capture");
      await expect(page.getByPlaceholder(/short note/i)).toHaveValue("");
      await page.getByTestId("capture-tab-outbox").click();
      await expect(page.getByTestId("capture-outbox-stats")).toContainText(/Pending:\s*0/);
      await expect(page.getByTestId("capture-outbox-list")).toHaveCount(0);
      await signOut(page);

      // --- the admin signs back in and still has the capture
      await loginAs(page, adminCreds.email, adminCreds.password);
      await page.goto("/capture");
      await page.getByTestId("capture-tab-outbox").click();
      const list = page.getByTestId("capture-outbox-list");
      await expect(list.locator("li")).toHaveCount(1);
    } finally {
      // Leave no row behind, even when the body failed midway (R1-D6.5).
      if (storedOutboxRow) {
        try {
          if (!(await page.getByTestId("chat-page").isVisible().catch(() => false))) {
            await loginAs(page, adminCreds.email, adminCreds.password);
          }
          await page.goto("/capture");
          await page.getByTestId("capture-tab-outbox").click();
          const discard = page.getByTestId("capture-outbox-list").getByRole("button", { name: "Discard" });
          while ((await discard.count()) > 0) {
            await discard.first().click();
            await page.waitForTimeout(150);
          }
        } catch {
          /* best effort cleanup */
        }
      }
    }
  });

  test("54a a second tab is signed out when the member signs out in the first", async ({ browser }) => {
    const context = await browser.newContext();
    try {
      const page1 = await context.newPage();
      await loginAs(page1, adminCreds.email, adminCreds.password);
      const page2 = await context.newPage();
      await page2.goto("/chat");
      await expect(page2.getByTestId("chat-page")).toBeVisible({ timeout: 30_000 });

      await signOut(page1);

      // No reload and no request: the `storage` event signs the other tab out.
      await expect(page2.getByRole("heading", { name: "Sign in" })).toBeVisible({ timeout: 2_000 });
    } finally {
      await context.close();
    }
  });
});
