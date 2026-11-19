// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-646 — inbox cleanup: open the per-mailbox "Clean up this mailbox" panel,
// start a date-range pass, and see progress. Hybrid: smoke login + page.route mocks
// (same pattern as email_organize.spec.ts).

import { test, expect, type Page } from "@playwright/test";

import {
  hasSmokeCreds,
  loginWithSmokeCredentials,
  smokeCredsSkipMessage,
} from "./smoke-auth";

const SOURCE_ID = "11111111-1111-1111-1111-111111111111";

function installMocks(page: Page): { started: () => boolean } {
  let started = false;

  page.route("**/sources**", async (route) => {
    if (route.request().method() === "GET") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          sources: [
            {
              id: SOURCE_ID,
              name: "Email (personal)",
              source_type: "imap",
              url: "imap://imap.gmail.com:993/personal",
              category: "email",
              active: true,
              poll_interval: 900,
              last_polled_at: null,
              last_signal_at: null,
              polling_active: true,
            },
          ],
          total: 1,
        }),
      });
      return;
    }
    await route.continue();
  });

  page.route("**/me/email-providers**", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        providers: [
          {
            id: "gmail",
            display_name: "Gmail",
            imap_host: "imap.gmail.com",
            imap_port: 993,
            smtp_host: "smtp.gmail.com",
            smtp_port: 587,
            app_password_url: "",
            help_steps: [],
            auth_status: "app_password",
            enabled: true,
            disabled_reason: null,
            notes: "",
            label_visible: true,
          },
        ],
      }),
    });
  });

  page.route("**/api/v1/me/email-taxonomy**", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        categories: [
          { slug: "promotional_bulk", label: "Promotional / bulk", enabled: true, is_custom: false, is_protected: false, sort: 0 },
          { slug: "other", label: "Other", enabled: true, is_custom: false, is_protected: true, sort: 1 },
        ],
      }),
    });
  });

  page.route("**/api/v1/me/email-classification-rules**", async (route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ rules: [] }) });
  });

  page.route("**/signals**", async (route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ signals: [], total: 0 }) });
  });

  page.route(`**/me/email-sources/${SOURCE_ID}/cleanup-spam`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ spam_count: 0, trash_available: false }),
    });
  });

  page.route(`**/me/email-sources/${SOURCE_ID}/cleanup/*`, async (route) => {
    // GET job progress
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        state: "backfilling",
        kind: "window",
        progress: 0.5,
        counts: { scanned: 20, classified: 20, labeled: 20, executed: 0 },
        proposals: [],
      }),
    });
  });

  page.route(`**/me/email-sources/${SOURCE_ID}/cleanup`, async (route) => {
    if (route.request().method() === "POST") {
      started = true;
      await route.fulfill({
        status: 202,
        contentType: "application/json",
        body: JSON.stringify({ job_id: "job-1", state: "backfilling" }),
      });
      return;
    }
    await route.continue();
  });

  return { started: () => started };
}

test.describe("email cleanup pass", () => {
  test.skip(!hasSmokeCreds, smokeCredsSkipMessage);

  test.beforeEach(async ({ page }) => {
    await loginWithSmokeCredentials(page);
  });

  test("open cleanup panel → start a date-range pass → progress shows", async ({ page }) => {
    const mocks = installMocks(page);
    await page.goto("/settings/email");
    await expect(page.getByTestId("email-signals-view")).toBeVisible({ timeout: 15_000 });

    await page.getByText("Clean up this mailbox").click();
    await expect(page.getByRole("heading", { name: "Clean up my inbox" })).toBeVisible();

    await page.getByRole("button", { name: "Start cleanup" }).click();

    await expect(page.getByTestId("cleanup-progress")).toBeVisible({ timeout: 15_000 });
    expect(mocks.started()).toBe(true);
  });
});
