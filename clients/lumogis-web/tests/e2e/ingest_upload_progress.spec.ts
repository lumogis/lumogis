// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-511 — Playwright smoke for library upload ingest progress UI.
// Real smoke login + mocked ingest upload/poll/batch routes (same hybrid pattern
// as document_chat.spec.ts) so stage labels and the batch counter are asserted
// without a live ingest worker, Qdrant, or extractor stack.

import { test, expect, type Page, type Route } from "@playwright/test";

import { hasSmokeCreds, loginWithSmokeCredentials, smokeCredsSkipMessage } from "./smoke-auth";

const JOB_ID = 9001;
const FILE_ID = "e2e-ingest-progress-file";
const FILE_NAME = "e2e-progress.txt";

function jobProgressBody(stage: "extracting" | "embedding" | "done") {
  const status = stage === "done" ? "done" : "running";
  const progress_pct = stage === "extracting" ? 15 : stage === "embedding" ? 60 : 100;
  return {
    job_id: JOB_ID,
    file_id: FILE_ID,
    batch_id: null,
    status,
    stage,
    progress_pct,
    status_message: null,
    error: null,
    enqueued_at: "2026-06-21T12:00:00+00:00",
    started_at: "2026-06-21T12:00:01+00:00",
    finished_at: stage === "done" ? "2026-06-21T12:00:05+00:00" : null,
  };
}

async function mockIngestUploadProgressRoutes(
  page: Page,
  state: { jobPollCount: number },
): Promise<void> {
  await page.route("**/api/v1/documents", async (route: Route) => {
    if (route.request().method() !== "GET") {
      await route.continue();
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ documents: [] }),
    });
  });

  await page.route("**/api/v1/ingest/upload", async (route: Route) => {
    if (route.request().method() !== "POST") {
      await route.continue();
      return;
    }
    await route.fulfill({
      status: 202,
      contentType: "application/json",
      body: JSON.stringify({ status: "queued", file_id: FILE_ID, job_id: JOB_ID }),
    });
  });

  await page.route(`**/api/v1/ingest/jobs/${JOB_ID}`, async (route: Route) => {
    state.jobPollCount += 1;
    const stage =
      state.jobPollCount < 2 ? "extracting" : state.jobPollCount < 4 ? "embedding" : "done";
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(jobProgressBody(stage)),
    });
  });

  await page.route("**/api/v1/ingest/batches/**", async (route: Route) => {
    if (route.request().method() !== "GET") {
      await route.continue();
      return;
    }
    const batchId = decodeURIComponent(new URL(route.request().url()).pathname.split("/").pop() ?? "");
    const done = state.jobPollCount >= 4;
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        batch_id: batchId,
        completed: done ? 1 : 0,
        failed: 0,
        in_progress: done ? 0 : 1,
      }),
    });
  });
}

test.describe("LUM-511 ingest upload progress (/documents)", () => {
  test.skip(!hasSmokeCreds, smokeCredsSkipMessage);

  test("upload shows stage progress bar and batch counter through Done", async ({ page }) => {
    const state = { jobPollCount: 0 };
    await mockIngestUploadProgressRoutes(page, state);
    await loginWithSmokeCredentials(page);

    await page.goto("/documents");
    await expect(page.getByTestId("document-upload-panel")).toBeVisible({ timeout: 15_000 });

    await page.locator('input[type="file"]').setInputFiles({
      name: FILE_NAME,
      mimeType: "text/plain",
      buffer: Buffer.from("LUM-511 ingest upload progress e2e.\n"),
    });

    await expect(page.getByLabel("Selected files").getByText(FILE_NAME)).toBeVisible({
      timeout: 10_000,
    });
    const progressBar = page.getByTestId("ingest-progress-bar");
    await expect(progressBar).toBeVisible({ timeout: 10_000 });

    // Stage label from mocked poll progression (extracting → embedding → done).
    await expect(
      progressBar.getByText(/Extracting text|Embedding|Done/),
    ).toBeVisible({ timeout: 15_000 });
    await expect(progressBar.getByText("Done")).toBeVisible({ timeout: 20_000 });

    const counter = page.getByTestId("ingest-batch-counter");
    await expect(counter).toContainText("of 1");
    await expect(counter).toContainText("1 of 1", { timeout: 15_000 });
  });
});
