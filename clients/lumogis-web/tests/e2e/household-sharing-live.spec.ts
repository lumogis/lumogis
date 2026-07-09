// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Live-stack two-user household sharing proofs (P1 closure):
//   LUM-582 — conversation share + cross-member visibility
//   LUM-583 — /me/shared-items owner list vs member empty
//   LUM-585 — non-owner "Shared by {member}" attribution on document detail
//
// Requires a running stack (make web-e2e-prove) with smoke admin + member creds.
// No route mocks — real Postgres, Qdrant, and session-end pipeline.

import { test, expect, type BrowserContext, type Page } from "@playwright/test";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { createEndedSession } from "./session-harness";
import {
  adminCreds,
  hasTwoUserCreds,
  loginAs,
  memberCreds,
  twoUserCredsSkipMessage,
} from "./two-user-auth";

const E2E_DIR = path.dirname(fileURLToPath(import.meta.url));
const SAMPLE_DOC = path.join(E2E_DIR, "demo", "fixtures", "household-insurance.md");
const SAMPLE_DOC_NAME = "household-insurance.md";
const ATTRIBUTION_LABEL = "Household Admin";
const EDITED_SUMMARY = `Household-facing roof quote e2e ${Date.now()}`;

test.describe.configure({ mode: "serial", timeout: 360_000 });

async function mintAccessToken(page: Page): Promise<string> {
  const origin = process.env.PLAYWRIGHT_BASE_URL ?? "http://127.0.0.1";
  const body = await page.evaluate(async (originHeader) => {
    const res = await fetch("/api/v1/auth/refresh", {
      method: "POST",
      credentials: "include",
      headers: { Origin: originHeader },
    });
    if (!res.ok) {
      throw new Error(`refresh failed HTTP ${res.status}`);
    }
    return (await res.json()) as { access_token?: string };
  }, origin);
  if (!body.access_token) throw new Error("refresh missing access_token");
  return body.access_token;
}

async function setAdminDisplayName(page: Page): Promise<void> {
  const token = await mintAccessToken(page);
  const me = await page.request.get("/api/v1/auth/me", {
    headers: { Authorization: `Bearer ${token}` },
  });
  expect(me.ok()).toBeTruthy();
  const userId = (await me.json()) as { id?: string };
  if (!userId.id) throw new Error("auth/me missing id");

  const patch = await page.request.patch(`/api/v1/admin/users/${userId.id}`, {
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
      Origin: process.env.PLAYWRIGHT_BASE_URL ?? "http://127.0.0.1",
    },
    data: { display_name: ATTRIBUTION_LABEL },
  });
  expect(patch.ok(), `display_name patch failed HTTP ${patch.status()}`).toBeTruthy();
}

async function uploadAndShareDocument(admin: Page): Promise<string> {
  await admin.goto("/documents");
  await admin.getByTestId("documents-page").waitFor({ timeout: 15_000 });
  await admin
    .locator('[data-testid="document-upload-panel"] input[type="file"]')
    .setInputFiles(SAMPLE_DOC);
  const row = admin.locator("tr[data-document-id]").filter({ hasText: SAMPLE_DOC_NAME }).first();
  await row.waitFor({ timeout: 120_000 });
  const docId = await row.getAttribute("data-document-id");
  if (!docId) throw new Error("upload row missing data-document-id");

  admin.on("dialog", (dialog) => void dialog.accept());
  await admin.goto(`/documents/${docId}`);
  await admin.getByTestId("share-toggle").getByRole("switch").click();
  await admin
    .getByText("Everyone in your household can find and read this.")
    .waitFor({ timeout: 30_000 });
  return docId;
}

test.describe("household sharing live (two-user)", () => {
  test.skip(!hasTwoUserCreds, twoUserCredsSkipMessage);

  test("LUM-582: admin shares edited conversation summary → member sees it", async ({
    browser,
  }) => {
    const adminCtx: BrowserContext = await browser.newContext();
    const admin = await adminCtx.newPage();
    let conversationId = "";
    try {
      await loginAs(admin, adminCreds.email, adminCreds.password);
      const created = await createEndedSession(admin, {
        messages: [
          { role: "user", content: "What did the roofer quote?" },
          { role: "assistant", content: "They quoted 2,400 for the garage roof." },
        ],
        maxAttempts: 120,
        intervalMs: 1000,
      });
      conversationId = created.conversation_id;

      await admin.goto("/chat");
      await expect(admin.getByTestId("chat-page")).toBeVisible({ timeout: 15_000 });
      await admin.reload();
      const sidebar = admin.getByTestId("conversation-sidebar");
      await expect(sidebar).toBeVisible({ timeout: 15_000 });
      await admin.getByTestId(`share-conversation-${conversationId}`).click();
      await admin.getByTestId("conversation-share-start").click();
      await admin.getByTestId("conversation-share-summary").fill(EDITED_SUMMARY);
      const publishWait = admin.waitForResponse(
        (res) =>
          res.url().includes("/publish") &&
          res.request().method() === "POST" &&
          res.status() === 200,
        { timeout: 30_000 },
      );
      await admin.getByTestId("conversation-share-confirm").click();
      await publishWait;
      await admin.reload();
      await expect(
        admin.getByTestId(`conversation-list-shared-badge-${conversationId}`),
      ).toBeVisible({ timeout: 15_000 });
    } finally {
      await adminCtx.close();
    }

    const memberCtx: BrowserContext = await browser.newContext();
    const member = await memberCtx.newPage();
    try {
      await loginAs(member, memberCreds.email, memberCreds.password);
      await member.goto("/chat");
      await expect(member.getByTestId("chat-page")).toBeVisible({ timeout: 15_000 });
      await member.reload();
      const sidebar = member.getByTestId("conversation-sidebar");
      await expect(sidebar).toBeVisible({ timeout: 15_000 });
      await expect(sidebar.getByText(EDITED_SUMMARY).first()).toBeVisible({ timeout: 30_000 });
      const sharedRow = sidebar.locator("[data-conversation-id]").filter({ hasText: EDITED_SUMMARY });
      await expect(sharedRow.getByRole("button", { name: /^Share / })).toHaveCount(0);
    } finally {
      await memberCtx.close();
    }
  });

  test("LUM-583: admin /me/shared-items lists shares; member sees empty", async ({
    browser,
  }) => {
    const adminCtx: BrowserContext = await browser.newContext();
    const admin = await adminCtx.newPage();
    let conversationId = "";
    try {
      await loginAs(admin, adminCreds.email, adminCreds.password);
      await uploadAndShareDocument(admin);

      const created = await createEndedSession(admin, { maxAttempts: 120, intervalMs: 1000 });
      conversationId = created.conversation_id;
      await admin.goto("/chat");
      await expect(admin.getByTestId("chat-page")).toBeVisible({ timeout: 15_000 });
      await admin.reload();
      const shareBtn = admin.getByTestId(`share-conversation-${conversationId}`);
      await expect(shareBtn).toBeVisible({ timeout: 15_000 });
      await shareBtn.click();
      await admin.getByTestId("conversation-share-start").click();
      const publishWait = admin.waitForResponse(
        (res) =>
          res.url().includes("/publish") &&
          res.request().method() === "POST" &&
          res.status() === 200,
        { timeout: 30_000 },
      );
      await admin.getByTestId("conversation-share-confirm").click();
      await publishWait;

      await admin.goto("/me/shared-items");
      await expect(admin.getByTestId("me-shared-items")).toBeVisible();
      await admin.reload();
      await expect(
        admin.getByRole("row").filter({ hasText: SAMPLE_DOC_NAME }).first(),
      ).toBeVisible({ timeout: 15_000 });
      await expect(admin.getByTestId(`unshare-sessions:${conversationId}`)).toBeVisible({
        timeout: 15_000,
      });
    } finally {
      await adminCtx.close();
    }

    const memberCtx: BrowserContext = await browser.newContext();
    const member = await memberCtx.newPage();
    try {
      await loginAs(member, memberCreds.email, memberCreds.password);
      await member.goto("/me/shared-items");
      await expect(member.getByTestId("me-shared-items")).toBeVisible();
      await expect(
        member.getByText("You haven't shared anything with your household yet."),
      ).toBeVisible({ timeout: 15_000 });
    } finally {
      await memberCtx.close();
    }
  });

  test("LUM-585: member sees Shared by attribution on shared document detail", async ({
    browser,
  }) => {
    const adminCtx: BrowserContext = await browser.newContext();
    const admin = await adminCtx.newPage();
    try {
      await loginAs(admin, adminCreds.email, adminCreds.password);
      await setAdminDisplayName(admin);
      await uploadAndShareDocument(admin);
    } finally {
      await adminCtx.close();
    }

    const memberCtx: BrowserContext = await browser.newContext();
    const member = await memberCtx.newPage();
    try {
      await loginAs(member, memberCreds.email, memberCreds.password);
      await member.goto("/documents");
      await expect(member.getByTestId("documents-page")).toBeVisible({ timeout: 15_000 });
      const sharedRow = member
        .locator("tr[data-document-id]")
        .filter({ hasText: SAMPLE_DOC_NAME })
        .first();
      await expect(sharedRow).toBeVisible({ timeout: 120_000 });
      const memberDocId = await sharedRow.getAttribute("data-document-id");
      if (!memberDocId) throw new Error("member library row missing data-document-id");
      await member.goto(`/documents/${memberDocId}`);
      await expect(member.getByTestId("document-detail")).toBeVisible({ timeout: 30_000 });
      await expect(member.getByTestId("share-indicator")).toContainText(
        `Shared by ${ATTRIBUTION_LABEL}`,
        { timeout: 30_000 },
      );
    } finally {
      await memberCtx.close();
    }
  });
});
