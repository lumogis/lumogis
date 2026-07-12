// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Labelled PNG captures of main Lumogis Web screens for UI review / marketing.
// Requires a running stack + smoke admin creds. Output: branding/screenshots/*.png

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { test, type Page } from "@playwright/test";

import {
  dismissOnboardingIfPresent,
  hasSmokeCreds,
  loginWithSmokeCredentials,
  smokeCredsSkipMessage,
} from "../smoke-auth";

const SPEC_DIR = path.dirname(fileURLToPath(import.meta.url));
const DEMO_DIR = path.resolve(SPEC_DIR, "../demo");
const SAMPLE_DOC = path.join(DEMO_DIR, "fixtures", "household-insurance.md");
const SAMPLE_DOC_NAME = "household-insurance.md";
const DEFAULT_OUT = path.resolve(SPEC_DIR, "../../../../../branding/screenshots");

const OUT_DIR = process.env.SCREENSHOTS_OUT_DIR
  ? path.resolve(process.env.SCREENSHOTS_OUT_DIR)
  : DEFAULT_OUT;

async function capture(page: Page, filename: string): Promise<void> {
  fs.mkdirSync(OUT_DIR, { recursive: true });
  const filePath = path.join(OUT_DIR, filename);
  await page.waitForLoadState("domcontentloaded");
  await page.screenshot({ path: filePath, fullPage: false, animations: "disabled" });
  console.log(`wrote ${filePath}`);
}

async function collapseSidebar(page: Page): Promise<void> {
  const collapse = page.getByRole("button", { name: "Collapse sidebar" });
  if (await collapse.isVisible().catch(() => false)) {
    await collapse.click();
    await page.locator(".lumogis-sidebarnav--collapsed").waitFor({ state: "visible", timeout: 5_000 });
    await page.locator(".lumogis-shell__body").evaluate((el) => {
      const cols = getComputedStyle(el).gridTemplateColumns;
      if (!cols.startsWith("64px")) {
        throw new Error(`Expected collapsed shell grid (64px …), got: ${cols}`);
      }
    });
  }
}

async function expandSidebar(page: Page): Promise<void> {
  const expand = page.getByRole("button", { name: "Expand sidebar" });
  if (await expand.isVisible().catch(() => false)) {
    await expand.click();
    await page.locator(".lumogis-sidebarnav--collapsed").waitFor({ state: "hidden", timeout: 5_000 });
  }
}

async function waitForShell(page: Page): Promise<void> {
  await page.getByTestId("lumogis-shell").waitFor({ timeout: 30_000 });
}

async function ensureSampleDoc(page: Page): Promise<void> {
  await page.goto("/documents");
  await page.getByTestId("documents-page").waitFor({ timeout: 15_000 });
  const row = page.locator("tr[data-document-id]").filter({ hasText: SAMPLE_DOC_NAME }).first();
  if (await row.isVisible().catch(() => false)) return;
  await page
    .locator('[data-testid="document-upload-panel"] input[type="file"]')
    .setInputFiles(SAMPLE_DOC);
  await row.waitFor({ timeout: 120_000 });
}

test("capture main screens", async ({ browser }) => {
  test.skip(!hasSmokeCreds, smokeCredsSkipMessage);

  // ── Login (unauthenticated) ─────────────────────────────────────────────────
  const anonCtx = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  const anon = await anonCtx.newPage();
  await anon.goto("/chat");
  await anon.getByLabel("Email").waitFor({ timeout: 15_000 });
  await capture(anon, "01-login.png");
  await anonCtx.close();

  // ── Authenticated surfaces (admin — sees full nav including Admin) ─────────
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  const page = await ctx.newPage();
  await loginWithSmokeCredentials(page);
  await dismissOnboardingIfPresent(page);

  await page.goto("/chat");
  await waitForShell(page);
  await page.getByTestId("chat-page").waitFor({ timeout: 15_000 });
  await capture(page, "02-chat.png");

  await page.goto("/search");
  await waitForShell(page);
  await page.getByPlaceholder("Search memories and entities…").waitFor({ timeout: 15_000 });
  await capture(page, "03-search.png");

  await page.goto("/documents");
  await waitForShell(page);
  await page.getByTestId("documents-page").waitFor({ timeout: 15_000 });
  await ensureSampleDoc(page);
  await page.goto("/documents");
  await page.getByTestId("documents-page").waitFor({ timeout: 15_000 });
  await capture(page, "04-documents-library.png");

  const firstDoc = page.locator("tr[data-document-id]").filter({ hasText: SAMPLE_DOC_NAME }).first();
  if (await firstDoc.isVisible().catch(() => false)) {
    const docId = await firstDoc.getAttribute("data-document-id");
    if (docId) {
      await page.goto(`/documents/${docId}`);
      await page.getByTestId("document-detail").waitFor({ timeout: 15_000 });
      await capture(page, "05-document-detail.png");

      await page.goto(`/documents/${docId}/chat`);
      await page.getByTestId("document-chat-page").waitFor({ timeout: 15_000 });
      await page.getByPlaceholder("Ask about this document…").waitFor({ timeout: 15_000 });
      await capture(page, "06-document-chat.png");
    }
  }

  await page.goto("/capture");
  await waitForShell(page);
  await page.getByTestId("quick-capture-page").waitFor({ timeout: 15_000 });
  await capture(page, "07-capture.png");

  await page.goto("/approvals");
  await waitForShell(page);
  await page.getByRole("heading", { name: /approvals/i }).waitFor({ timeout: 15_000 });
  await capture(page, "08-approvals.png");

  await page.goto("/me/profile");
  await waitForShell(page);
  await page.getByRole("navigation", { name: /^settings$/i }).waitFor({ timeout: 15_000 });
  await capture(page, "09-settings-profile.png");

  await page.goto("/me/shared-items");
  await waitForShell(page);
  await page.getByTestId("me-shared-items").waitFor({ timeout: 15_000 });
  await capture(page, "10-settings-shared-items.png");

  await page.goto("/audit");
  await waitForShell(page);
  await page.getByRole("heading", { name: /my activity/i }).waitFor({ timeout: 15_000 });
  await page.getByTestId("audit-member-filters").waitFor({ timeout: 15_000 });
  await collapseSidebar(page);
  await capture(page, "11-audit-log.png");
  await expandSidebar(page);

  await page.goto("/me/appearance");
  await waitForShell(page);
  await page.getByRole("heading", { name: /^appearance$/i }).waitFor({ timeout: 15_000 });
  await capture(page, "15-settings-appearance.png");

  await page.goto("/admin/audit");
  await waitForShell(page);
  await page.getByRole("heading", { name: /^household audit$/i }).waitFor({ timeout: 15_000 });
  await capture(page, "16-admin-household-audit.png");

  await page.goto("/admin/users");
  await waitForShell(page);
  await page.getByRole("heading", { name: "Users" }).waitFor({ timeout: 15_000 });
  await capture(page, "12-admin-users.png");

  await page.goto("/admin/shared-items");
  await waitForShell(page);
  await page.getByTestId("admin-shared-items").waitFor({ timeout: 15_000 });
  await capture(page, "13-admin-shared-items.png");

  await page.goto("/admin/system-status");
  await waitForShell(page);
  await page
    .getByRole("heading", { name: /^system status$/i, level: 2 })
    .waitFor({ timeout: 15_000 });
  await page.getByText(/^loading/i).waitFor({ state: "hidden", timeout: 60_000 }).catch(() => {});
  await capture(page, "14-admin-system-status.png");

  await ctx.close();
});
