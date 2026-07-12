// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-608 — live-stack Playwright: capture inbox → commit → archive (LUM-606/607).
// Happy path + failed→Retry: postgres seeds `status=failed` + `last_error` (same shape as
// orchestrator after embed/Qdrant failure); Retry issues a real POST …/index on the live stack.
//
//   export LUMOGIS_WEB_SMOKE_EMAIL=...
//   export LUMOGIS_WEB_SMOKE_PASSWORD='...'   # ≥12 chars
//
// Optional: PLAYWRIGHT_BASE_URL=http://127.0.0.1

import { test, expect, type Page } from "@playwright/test";

import { markCaptureIndexFailed } from "./e2e-postgres";
import { hasSmokeCreds, loginWithSmokeCredentials, smokeCredsSkipMessage, smokeEmail } from "./smoke-auth";

const FAILED_INDEX_ERROR = "index_memory_unavailable";

async function assertArchiveReadOnlyDetail(
  page: Page,
  unique: string,
  body: string,
): Promise<void> {
  const archiveDetail = page.getByRole("region", { name: "Committed capture" });
  await expect(archiveDetail).toBeVisible({ timeout: 30_000 });
  await expect(archiveDetail.getByText(unique)).toBeVisible();
  await expect(archiveDetail.getByText(body)).toBeVisible();
  await expect(archiveDetail.getByText(/committed to memory/i)).toBeVisible();
  await expect(archiveDetail.getByText(/note [0-9a-f]{8}/i)).toBeVisible();

  await expect(archiveDetail.getByRole("button")).toHaveCount(1);
  await expect(archiveDetail.getByRole("button", { name: /^close$/i })).toBeVisible();
  await expect(
    archiveDetail.getByRole("button", { name: /commit|delete|save|retry/i }),
  ).toHaveCount(0);
}

async function createTextCaptureOnCompose(
  page: Page,
  label: string,
): Promise<{ unique: string; body: string; captureId: string }> {
  const unique = `LUM-608 ${label} ${Date.now()}`;
  const body = `Household capture e2e body ${Date.now()}`;

  await page.goto("/capture");
  await expect(page.getByTestId("quick-capture-page")).toBeVisible({ timeout: 15_000 });

  await page.getByLabel("Title (optional)").fill(unique);
  await page.getByPlaceholder("Short note (required unless you add a URL below)").fill(body);

  const createWait = page.waitForResponse(
    (res) =>
      res.request().method() === "POST" &&
      res.url().includes("/api/v1/captures") &&
      !res.url().includes("/index"),
    { timeout: 60_000 },
  );
  await page.getByTestId("quick-capture-save-server").click();
  const createRes = await createWait;
  expect(createRes.ok(), `create capture failed HTTP ${createRes.status()}`).toBeTruthy();

  const created = (await createRes.json()) as { capture_id?: string };
  expect(created.capture_id, "create response must include capture_id").toBeTruthy();

  return { unique, body, captureId: created.capture_id! };
}

async function commitFromInboxDetail(page: Page, buttonName: RegExp): Promise<void> {
  const indexWait = page.waitForResponse(
    (res) =>
      res.request().method() === "POST" && /\/api\/v1\/captures\/[^/]+\/index$/.test(res.url()),
    { timeout: 120_000 },
  );
  const inboxDetail = page.getByRole("region", { name: "Capture detail" });
  await inboxDetail.getByRole("button", { name: buttonName }).click();
  const indexRes = await indexWait;
  expect(indexRes.ok(), `index capture failed HTTP ${indexRes.status()}`).toBeTruthy();
}

async function runInboxToArchiveFlow(page: Page, label: string): Promise<void> {
  const { unique, body } = await createTextCaptureOnCompose(page, label);

  await page.getByTestId("capture-tab-inbox").click();
  const inboxCard = page.getByRole("button").filter({ hasText: unique });
  await expect(inboxCard).toBeVisible({ timeout: 30_000 });
  await expect(inboxCard).toContainText("pending");

  await inboxCard.click();
  const inboxDetail = page.getByRole("region", { name: "Capture detail" });
  await expect(inboxDetail).toBeVisible();
  await expect(inboxDetail.getByRole("button", { name: /^commit to memory$/i })).toBeVisible();

  await commitFromInboxDetail(page, /^commit to memory$/i);

  await expect(inboxDetail).toBeHidden({ timeout: 30_000 });
  await expect(page.getByRole("button").filter({ hasText: unique })).toHaveCount(0, {
    timeout: 30_000,
  });

  await page.getByTestId("capture-tab-archive").click();
  const archiveCard = page.getByRole("button").filter({ hasText: unique });
  await expect(archiveCard).toBeVisible({ timeout: 60_000 });
  await expect(archiveCard).toContainText("indexed");

  await archiveCard.click();
  await assertArchiveReadOnlyDetail(page, unique, body);
}

async function runFailedRetryToArchiveFlow(page: Page, label: string): Promise<void> {
  const { unique, body, captureId } = await createTextCaptureOnCompose(page, label);

  markCaptureIndexFailed(captureId, smokeEmail, FAILED_INDEX_ERROR);

  await page.getByTestId("capture-tab-inbox").click();
  const inboxCard = page.getByRole("button").filter({ hasText: unique });
  await expect(inboxCard).toBeVisible({ timeout: 30_000 });
  await expect(inboxCard).toContainText("failed");
  await expect(inboxCard).toContainText(FAILED_INDEX_ERROR);

  await inboxCard.click();
  const inboxDetail = page.getByRole("region", { name: "Capture detail" });
  await expect(inboxDetail).toBeVisible();
  await expect(inboxDetail.getByText(/last attempt failed/i)).toBeVisible();
  await expect(inboxDetail.getByText(FAILED_INDEX_ERROR)).toBeVisible();
  await expect(
    inboxDetail.getByRole("button", { name: /^retry — add to memory$/i }),
  ).toBeVisible();

  await commitFromInboxDetail(page, /^retry — add to memory$/i);

  await expect(inboxDetail).toBeHidden({ timeout: 30_000 });
  await expect(page.getByRole("button").filter({ hasText: unique })).toHaveCount(0, {
    timeout: 30_000,
  });

  await page.getByTestId("capture-tab-archive").click();
  const archiveCard = page.getByRole("button").filter({ hasText: unique });
  await expect(archiveCard).toBeVisible({ timeout: 60_000 });
  await expect(archiveCard).toContainText("indexed");

  await archiveCard.click();
  await assertArchiveReadOnlyDetail(page, unique, body);
}

test.describe("LUM-608 capture inbox + archive live (desktop)", () => {
  test.skip(!hasSmokeCreds, smokeCredsSkipMessage);

  test("create → inbox → commit → archive read-only provenance", async ({ page }) => {
    test.setTimeout(300_000);
    await loginWithSmokeCredentials(page);
    await runInboxToArchiveFlow(page, "desktop");
  });

  test("failed inbox row → Retry → archive (simulated index failure)", async ({ page }) => {
    test.setTimeout(300_000);
    await loginWithSmokeCredentials(page);
    await runFailedRetryToArchiveFlow(page, "desktop-retry");
  });
});

test.describe("LUM-608 capture inbox + archive live (mobile)", () => {
  test.use({ viewport: { width: 390, height: 844 } });
  test.skip(!hasSmokeCreds, smokeCredsSkipMessage);

  test("create → inbox → commit → archive on mobile viewport", async ({ page }) => {
    test.setTimeout(300_000);
    await loginWithSmokeCredentials(page);
    await runInboxToArchiveFlow(page, "mobile");
  });
});
