// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-645 / LUM-679 — opt-in mailbox organization: open the per-mailbox
// "Organize this mailbox" panel, enable labelling, Save → 202 PATCH.
// Hybrid: smoke login + page.route mocks (same pattern as email_imap_sources.spec.ts).

import { test, expect, type Page } from "@playwright/test";

import {
  hasSmokeCreds,
  loginWithSmokeCredentials,
  smokeCredsSkipMessage,
} from "./smoke-auth";

const SOURCE_ID = "11111111-1111-1111-1111-111111111111";

function installMocks(page: Page): { organizePatched: () => boolean } {
  let patched = false;

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

  page.route(`**/me/email-sources/${SOURCE_ID}/organize`, async (route) => {
    if (route.request().method() === "PATCH") {
      patched = true;
      await route.fulfill({
        status: 202,
        contentType: "application/json",
        body: JSON.stringify({ updated: true, apply_to_existing: false }),
      });
      return;
    }
    await route.continue();
  });

  return { organizePatched: () => patched };
}

test.describe("email organize settings", () => {
  test.skip(!hasSmokeCreds, smokeCredsSkipMessage);

  test.beforeEach(async ({ page }) => {
    await loginWithSmokeCredentials(page);
  });

  test("open organize panel → enable label → Save PATCHes", async ({ page }) => {
    const mocks = installMocks(page);
    await page.goto("/settings/email");
    await expect(page.getByTestId("email-signals-view")).toBeVisible({ timeout: 15_000 });

    // Open the per-mailbox organize panel.
    await page.getByText("Organize this mailbox").click();
    await expect(page.getByRole("heading", { name: "Organize my mailbox" })).toBeVisible();

    // Enable labelling and save.
    await page.getByRole("checkbox", { name: /Label my emails/ }).check();
    await page.getByRole("button", { name: "Save" }).click();

    await expect(page.getByTestId("organize-save-msg")).toContainText(/Lumogis will organize/i, {
      timeout: 15_000,
    });
    expect(mocks.organizePatched()).toBe(true);
  });
});
