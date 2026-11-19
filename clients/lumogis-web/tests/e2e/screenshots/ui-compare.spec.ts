// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Before/after captures for a redesign pass (LUM-993). Run it twice with the
// same account and data: once against the current build ("before") and once
// against the pass build ("after"), then join the two folders into one page
// with `node scripts/ui-compare.mjs page`.
//
//   UI_COMPARE_BASE_URL  where Lumogis Web runs (required)
//   UI_COMPARE_OUT       output folder for the PNGs (required)
//   UI_COMPARE_ROUTES    comma-separated paths; `/documents/:first` opens the
//                        first document in the library; `path@more` opens the
//                        phone More sheet, `path@collapsed` collapses the sidebar,
//                        `path@chats` opens the phone chat list and `path@history`
//                        opens the newest closed chat
//   UI_COMPARE_SHELL_MOCK optional JSON answer for GET /api/v1/me/shell
//   UI_COMPARE_MOCKS     optional path to a JSON file of GET answers, so both
//                        sides show the same data the account lacks (a mailbox,
//                        some rules): [{"url": "**/api/v1/...", "body": {...}}].
//                        Only GETs are answered; anything else reaches the server.
//   LUMOGIS_WEB_SMOKE_EMAIL / LUMOGIS_WEB_SMOKE_PASSWORD  the account to sign in as
//
// Each route is captured at 1280 px light, 1280 px dark and iPhone 13 light,
// with every (i) closed. Not an assertion suite: a failed route is logged and
// skipped so one broken page does not lose the rest of the comparison.

import fs from "node:fs";
import path from "node:path";

import { devices, test, type Browser, type Page } from "@playwright/test";

import { dismissOnboardingIfPresent, hasSmokeCreds, loginWithSmokeCredentials, smokeCredsSkipMessage } from "../smoke-auth";

const BASE_URL = process.env.UI_COMPARE_BASE_URL ?? "";
const OUT = process.env.UI_COMPARE_OUT ?? "";
const ROUTES = (process.env.UI_COMPARE_ROUTES ?? "/chat")
  .split(",")
  .map((r) => r.trim())
  .filter(Boolean);

// iPhone 13 size and touch, in Chromium (the device preset would ask for WebKit).
const { viewport, userAgent, deviceScaleFactor, isMobile, hasTouch } = devices["iPhone 13"];
const iphone = { viewport, userAgent, deviceScaleFactor, isMobile, hasTouch };

const MODES = [
  { name: "desktop-light", theme: "light", context: { viewport: { width: 1280, height: 800 } } },
  { name: "desktop-dark", theme: "dark", context: { viewport: { width: 1280, height: 800 } } },
  { name: "phone-light", theme: "light", context: iphone },
] as const;

/** "/admin/system-status" → "admin.system-status"; "/documents/:first" → "documents.first"; "/" → "home". */
function slug(route: string): string {
  const s = route.replace(/^\/+|\/+$/g, "").replace(/:/g, "").replace(/\//g, ".").replace(/[^a-z0-9.-]+/gi, "-");
  return s.toLowerCase() || "home";
}

async function resolveRoute(page: Page, route: string): Promise<string | null> {
  if (route !== "/documents/:first") return route;
  await page.goto("/documents");
  const row = page.locator("tr[data-document-id]").first();
  if (!(await row.isVisible({ timeout: 15_000 }).catch(() => false))) return null;
  const id = await row.getAttribute("data-document-id");
  return id ? `/documents/${id}` : null;
}

async function settle(page: Page): Promise<void> {
  await page.waitForLoadState("domcontentloaded");
  // Streaming endpoints keep the network busy; give up on idle after a few seconds.
  await page.waitForLoadState("networkidle", { timeout: 8_000 }).catch(() => {});
  await page.evaluate(() => document.fonts.ready);
  await page.waitForTimeout(400);
}

async function captureMode(browser: Browser, mode: (typeof MODES)[number]): Promise<void> {
  const context = await browser.newContext({ ...mode.context, baseURL: BASE_URL, reducedMotion: "reduce" });
  await context.addInitScript((theme) => {
    try {
      localStorage.setItem("lumogis-theme", theme);
    } catch {
      /* private mode: the page falls back to the system theme */
    }
  }, mode.theme);
  const page = await context.newPage();
  // Optional: answer GET /api/v1/me/shell with fixed JSON, for a back end that
  // predates it (LUM-993 Pass 2a). The page notes when this was used.
  const shellMock = process.env.UI_COMPARE_SHELL_MOCK;
  if (shellMock) {
    await page.route("**/api/v1/me/shell", (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: shellMock }),
    );
  }
  // Optional: fixed GET answers from a file (LUM-993 plan B), the same for both sides.
  const mocksFile = process.env.UI_COMPARE_MOCKS;
  if (mocksFile) {
    const mocks = JSON.parse(fs.readFileSync(mocksFile, "utf8")) as Array<{ url: string; body: unknown }>;
    for (const m of mocks) {
      await page.route(m.url, (route) =>
        // Data requests only: a page load of /settings/sources must still get the app.
        route.request().method() === "GET" && route.request().resourceType() !== "document"
          ? route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(m.body) })
          : route.fallback(),
      );
    }
  }
  await loginWithSmokeCredentials(page);
  await dismissOnboardingIfPresent(page);

  for (const route of ROUTES) {
    const file = path.join(OUT, `${mode.name}__${slug(route)}.png`);
    try {
      // "route@more" opens the phone More sheet, "route@collapsed" collapses the
      // sidebar before the capture (LUM-993 Pass 2a). Each only acts where its
      // control exists, so the same list works on the old and the new shell.
      const [path_, action] = route.split("@") as [string, string | undefined];
      const target = await resolveRoute(page, path_);
      if (target === null) {
        console.warn(`skip ${route} (${mode.name}): nothing to open`);
        continue;
      }
      await page.goto(target);
      await settle(page);
      await page.keyboard.press("Escape"); // closes any (i) or menu left open
      const control =
        action === "more"
          ? page.getByRole("button", { name: /^More/ })
          : action === "collapsed"
            ? page.getByRole("button", { name: "Collapse sidebar" })
            : action === "chats"
              ? page.getByRole("button", { name: "Chats" })
              : null;
      const acted = control !== null && (await control.isVisible().catch(() => false));
      if (acted) {
        await control.click();
        await page.waitForTimeout(400);
      }
      if (action === "history") {
        // Open the newest closed chat so the capture shows a conversation (LUM-993 Pass 2b).
        const chats = page.getByRole("button", { name: "Chats" });
        if (await chats.isVisible().catch(() => false)) await chats.click();
        const first = page.getByTestId("conversation-sidebar").locator("[data-conversation-id] button").first();
        if (await first.isVisible({ timeout: 10_000 }).catch(() => false)) {
          await first.click();
          await page.waitForTimeout(1_500);
        }
      }
      await page.screenshot({ path: file, fullPage: false, animations: "disabled" });
      if (acted && action === "collapsed") await page.getByRole("button", { name: "Expand sidebar" }).click();
      if (acted && (action === "more" || action === "chats")) await page.keyboard.press("Escape");
      console.log(`wrote ${file}`);
    } catch (err) {
      console.warn(`skip ${route} (${mode.name}): ${(err as Error).message.split("\n")[0]}`);
    }
  }
  await context.close();
}

test("ui-compare captures", async ({ browser }) => {
  test.skip(!hasSmokeCreds, smokeCredsSkipMessage);
  test.skip(!BASE_URL || !OUT, "Set UI_COMPARE_BASE_URL and UI_COMPARE_OUT.");
  test.setTimeout(30_000 + ROUTES.length * MODES.length * 30_000);
  fs.mkdirSync(OUT, { recursive: true });
  // One mode at a time: the smoke account has one active refresh token, so
  // parallel sign-ins would sign each other out.
  for (const mode of MODES) await captureMode(browser, mode);
});
