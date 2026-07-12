// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-160 — Playwright smoke for /documents library UI.
// LUM-157 — single-user household share lifecycle (publish → badge → filter → unpublish).
// Requires live stack + smoke credentials (same contract as chat-conversation-history.spec.ts).

import { test, expect, type Page, type Route } from "@playwright/test";

import {
  hasSmokeCreds,
  loginWithSmokeCredentials,
  smokeCredsSkipMessage,
} from "./smoke-auth";

test.describe("documents library", () => {
  test.skip(!hasSmokeCreds, smokeCredsSkipMessage);

  test.beforeEach(async ({ page }) => {
    await loginWithSmokeCredentials(page);
  });

  test("library route renders empty or list state", async ({ page }) => {
    await page.goto("/documents");
    await expect(page.getByTestId("documents-page")).toBeVisible({
      timeout: 15_000,
    });
  });
});

// ---------------------------------------------------------------------------
// LUM-157 — household document sharing lifecycle.
//
// Hybrid pattern (same as document_chat.spec.ts / ingest_upload_progress.spec.ts):
// real smoke login + stateful page.route mocks for the documents + publish routes,
// so the full owner UI flow — confirm-then-share, "Shared" badge, "Shared with
// household" filter, and unshare-reverts — is asserted deterministically without a
// live share_document job, Qdrant projection, or a second household member.
// (Two-user cross-visibility is proven in the core 1.5.10 pytest integration test.)

const SHARED_DOC_ID = 4157;
const OTHER_DOC_ID = 4158;
const SHARED_DOC_NAME = "Household budget 2026.pdf";
const OTHER_DOC_NAME = "Private notes.md";

type ShareStatus = "personal" | "sharing" | "shared" | "unsharing" | "partial";

interface ShareMockState {
  sharedStatus: ShareStatus;
}

function summary(
  documentId: number,
  displayName: string,
  shareStatus: ShareStatus,
): Record<string, unknown> {
  return {
    document_id: documentId,
    display_name: displayName,
    file_path: `/library/${displayName}`,
    file_type: displayName.split(".").pop() ?? "txt",
    chunk_count: 3,
    entity_count: 0,
    scope: "personal",
    status: "indexed",
    indexed_at: "2026-07-06T12:00:00+00:00",
    error_message: null,
    share_status: shareStatus,
    in_flight_share_job_id: null,
    is_owner: true,
  };
}

function detail(
  documentId: number,
  displayName: string,
  shareStatus: ShareStatus,
): Record<string, unknown> {
  return {
    ...summary(documentId, displayName, shareStatus),
    file_hash: "deadbeef",
    entities: [],
    source_available: true,
  };
}

async function mockShareRoutes(page: Page, state: ShareMockState): Promise<void> {
  // Auto-accept the plain-language share confirm (window.confirm in ShareToggle).
  page.on("dialog", (dialog) => void dialog.accept());

  // List — the shared doc (current lifecycle state) + a control personal doc.
  await page.route("**/api/v1/documents", async (route: Route) => {
    if (route.request().method() !== "GET") {
      await route.continue();
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        documents: [
          summary(SHARED_DOC_ID, SHARED_DOC_NAME, state.sharedStatus),
          summary(OTHER_DOC_ID, OTHER_DOC_NAME, "personal"),
        ],
      }),
    });
  });

  // Publish / unpublish the shared doc — 202 + job id; flip the mock lifecycle
  // state so the subsequent list/detail refetch reflects the new share status.
  await page.route(
    `**/api/v1/documents/${SHARED_DOC_ID}/publish`,
    async (route: Route) => {
      const method = route.request().method();
      if (method === "POST") {
        state.sharedStatus = "shared";
        await route.fulfill({
          status: 202,
          contentType: "application/json",
          body: JSON.stringify({
            document_id: SHARED_DOC_ID,
            job_id: 7157,
            share_status: "sharing",
          }),
        });
        return;
      }
      if (method === "DELETE") {
        state.sharedStatus = "personal";
        await route.fulfill({
          status: 202,
          contentType: "application/json",
          body: JSON.stringify({
            document_id: SHARED_DOC_ID,
            job_id: 7158,
            share_status: "unsharing",
          }),
        });
        return;
      }
      await route.continue();
    },
  );

  // Detail — exact id path (glob * is one segment; /publish above is more specific).
  await page.route(`**/api/v1/documents/${SHARED_DOC_ID}`, async (route: Route) => {
    if (route.request().method() !== "GET") {
      await route.continue();
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(detail(SHARED_DOC_ID, SHARED_DOC_NAME, state.sharedStatus)),
    });
  });
}

test.describe("LUM-157 household document sharing (/documents)", () => {
  test.skip(!hasSmokeCreds, smokeCredsSkipMessage);

  test("owner shares a document: confirm → badge → filter → unshare reverts", async ({
    page,
  }) => {
    const state: ShareMockState = { sharedStatus: "personal" };
    await mockShareRoutes(page, state);
    await loginWithSmokeCredentials(page);

    const sharedRow = () => page.locator(`tr[data-document-id="${SHARED_DOC_ID}"]`);
    const otherRow = () => page.locator(`tr[data-document-id="${OTHER_DOC_ID}"]`);
    const filter = () => page.getByTestId("documents-shared-filter");

    // Library: both docs listed; the target starts "Personal".
    await page.goto("/documents");
    await expect(page.getByTestId("documents-page")).toBeVisible({ timeout: 15_000 });
    await expect(sharedRow()).toContainText("Personal");

    // Filter before sharing: nothing shared yet → empty-shared message, both hidden.
    await filter().check();
    await expect(page.getByTestId("documents-shared-empty")).toBeVisible();
    await filter().uncheck();

    // Detail: owner sees the interactive toggle, unchecked + "Personal".
    await page.goto(`/documents/${SHARED_DOC_ID}`);
    await expect(page.getByRole("heading", { name: SHARED_DOC_NAME })).toBeVisible({
      timeout: 15_000,
    });
    const toggle = page.getByTestId("share-toggle");
    const shareSwitch = toggle.getByRole("switch");
    await expect(shareSwitch).not.toBeChecked();
    await expect(toggle).toContainText("Personal");

    // Share: confirm dialog auto-accepted → flips to "Shared".
    await shareSwitch.click();
    await expect(shareSwitch).toBeChecked({ timeout: 15_000 });
    await expect(toggle).toContainText("Shared");
    await expect(
      page.getByText("Everyone in your household can find and read this."),
    ).toBeVisible();

    // Library badge now reads "Shared"; the filter keeps only the shared doc.
    await page.goto("/documents");
    await expect(sharedRow()).toContainText("Shared", { timeout: 15_000 });
    await filter().check();
    await expect(sharedRow()).toBeVisible();
    await expect(otherRow()).toHaveCount(0);
    await filter().uncheck();

    // Unshare: switch is checked (no confirm on unshare) → reverts to "Personal".
    await page.goto(`/documents/${SHARED_DOC_ID}`);
    await expect(shareSwitch).toBeChecked({ timeout: 15_000 });
    await shareSwitch.click();
    await expect(shareSwitch).not.toBeChecked({ timeout: 15_000 });
    await expect(toggle).toContainText("Personal");
    await expect(
      page.getByText("Everyone in your household can find and read this."),
    ).toHaveCount(0);

    // Library reverts; the filter now shows the empty-shared state again.
    await page.goto("/documents");
    await expect(sharedRow()).toContainText("Personal", { timeout: 15_000 });
    await filter().check();
    await expect(page.getByTestId("documents-shared-empty")).toBeVisible();
  });
});
