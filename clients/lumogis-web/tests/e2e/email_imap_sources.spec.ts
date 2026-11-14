// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-627 — Email IMAP flow: add mailbox → sync now → category chip visible.
// Hybrid: smoke login + page.route mocks (same pattern as documents.spec.ts).

import { test, expect, type Page } from "@playwright/test";

import {
  hasSmokeCreds,
  loginWithSmokeCredentials,
  smokeCredsSkipMessage,
} from "./smoke-auth";

const SOURCE_ID = "11111111-1111-1111-1111-111111111111";

function installEmailMocks(page: Page): void {
  let hasSource = false;

  page.route("**/me/email-sources", async (route) => {
    if (route.request().method() === "POST") {
      hasSource = true;
      await route.fulfill({
        status: 201,
        contentType: "application/json",
        body: JSON.stringify({
          status: "created",
          source_id: SOURCE_ID,
          source_type: "imap",
          url: "imap://imap.gmail.com:993/personal",
          extraction_method: "imap_metadata",
        }),
      });
      return;
    }
    await route.continue();
  });

  // LUM-644 Step 1 — the setup form fetches the provider list and renders a
  // picker before any credential field exists. Mocked so the spec does not
  // depend on which providers the live backend happens to enable.
  page.route("**/api/v1/me/email-providers", async (route) => {
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
            app_password_url: "https://example.invalid/app-passwords",
            help_steps: [],
            auth_status: "app_password",
            // Falsy supports_oauth keeps this on the app-password branch;
            // LUM-687's OAuth device flow is a different surface.
            enabled: true,
            disabled_reason: null,
            notes: "",
          },
        ],
      }),
    });
  });

  // LUM-644 — save is gated on a successful probe, so "Add mailbox" stays
  // disabled until this returns. Bare-prefixed: postJson("/me/email-sources/test").
  page.route("**/me/email-sources/test", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        ok: true,
        message_count: 12,
        // Empty: a non-empty list mounts the folder-seeding fieldset, which is
        // LUM-6xx label seeding, not what this spec is proving.
        folders: [],
        capabilities: [],
        error: null,
        proxy_unverified: false,
      }),
    });
  });

  page.route("**/api/v1/me/connector-credentials/imap**", async (route) => {
    if (route.request().method() === "PUT") {
      await route.fulfill({ status: 200, contentType: "application/json", body: "{}" });
      return;
    }
    await route.continue();
  });

  page.route("**/sources**", async (route) => {
    const url = route.request().url();
    if (url.includes("/sync") && route.request().method() === "POST") {
      await route.fulfill({
        status: 202,
        contentType: "application/json",
        body: JSON.stringify({ status: "queued", source_id: SOURCE_ID }),
      });
      return;
    }
    if (route.request().method() === "GET") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          sources: hasSource
            ? [
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
              ]
            : [],
          total: hasSource ? 1 : 0,
        }),
      });
      return;
    }
    await route.continue();
  });

  page.route("**/api/v1/me/email-taxonomy**", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        categories: [
          { slug: "school", label: "School", enabled: true, is_custom: false, is_protected: false, sort: 0 },
        ],
      }),
    });
  });

  page.route("**/api/v1/me/email-classification-rules**", async (route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ rules: [] }) });
  });

  page.route("**/signals**", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        signals: [
          {
            signal_id: "sig-1",
            source_id: SOURCE_ID,
            title: "Field trip permission",
            url: `imap://${SOURCE_ID}/1/42`,
            category: "school",
            category_label: "School",
            scope: "personal",
            published_at: null,
            content_summary: "",
            entities: [],
            topics: [],
            importance_score: 0.5,
            relevance_score: 0.5,
            notified: false,
            created_at: "2026-07-19T12:00:00Z",
            source_url: null,
            source_label: null,
          },
        ],
        total: 1,
      }),
    });
  });
}

test.describe("email IMAP sources", () => {
  test.skip(!hasSmokeCreds, smokeCredsSkipMessage);

  test.beforeEach(async ({ page }) => {
    installEmailMocks(page);
    await loginWithSmokeCredentials(page);
  });

  test("add mailbox → sync now → school category chip visible", async ({ page }) => {
    await page.goto("/me/email-signals");
    await expect(page.getByTestId("email-signals-view")).toBeVisible({ timeout: 15_000 });

    // LUM-644 turned this into a guided on-ramp. Before it, the credential
    // fields were on screen immediately; now `if (!provider)` renders a picker
    // and nothing else, so filling "Username" waits 120s for an input that
    // cannot exist yet. Pick the provider first.
    await page.getByRole("button", { name: "Gmail" }).click();

    // "Mailbox label" is a slug ([a-z0-9_-]{1,64}), not display copy — a human
    // name here trips client-side validation, so the POST never fires and the
    // failure surfaces later as a missing "Sync now" button. The human name is
    // the separate optional field below.
    await page.getByLabel("Mailbox label").fill("personal");
    await page.getByLabel("Display name (optional)").fill("Email (personal)");
    await page.getByLabel("Username").fill("alice@gmail.com");
    await page.getByLabel("App password").fill("app-password-secret");

    // Save is gated on a successful probe (`canSave`), so clicking straight
    // through would hit a disabled button.
    await page.getByRole("button", { name: "Test connection" }).click();
    const addMailbox = page.getByRole("button", { name: "Add mailbox" });
    await expect(addMailbox).toBeEnabled({ timeout: 15_000 });
    await addMailbox.click();

    await expect(page.getByLabel("Sync now Email (personal)")).toBeVisible({ timeout: 15_000 });
    await page.getByLabel("Sync now Email (personal)").click();
    // Scoped, not bare: the probe leaves "Connected — N messages" on screen as a
    // second role="status", so an unscoped match is a strict-mode violation.
    await expect(page.getByRole("status").filter({ hasText: /sync queued/i })).toBeVisible();

    await expect(page.getByTestId("category-chip").filter({ hasText: "School" })).toBeVisible({
      timeout: 15_000,
    });
    await expect(page.getByText("Field trip permission")).toBeVisible();
  });
});
