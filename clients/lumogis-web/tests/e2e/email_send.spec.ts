// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-680 — send UX e2e: reply → composer → confirm → Send / Save-to-Drafts,
// with the first-contact banner. Hybrid: smoke login + page.route mocks (same
// pattern as email_cleanup.spec.ts / email_organize.spec.ts) — no live mailbox
// is needed, only a running web app + a logged-in session. Creds-gated: skips
// cleanly without LUMOGIS_WEB_SMOKE_* (run under `make web-e2e-prove`).

import { test, expect, type Page } from "@playwright/test";

import {
  hasSmokeCreds,
  loginWithSmokeCredentials,
  smokeCredsSkipMessage,
} from "./smoke-auth";

const SOURCE_ID = "11111111-1111-1111-1111-111111111111";
const SIGNAL_ID = "sig-e2e-send-1";
const DRAFT = "Sure — Friday works for me.";

interface Recorder {
  created: () => Array<{ action_name: string }>;
  executed: () => number[];
  canceled: () => number[];
}

function installMocks(
  page: Page,
  opts: { firstContact?: boolean; executeSuccess?: boolean } = {},
): Recorder {
  const created: Array<{ action_name: string }> = [];
  const executed: number[] = [];
  const canceled: number[] = [];
  let nextId = 100;

  // One imap mailbox so the Email surface is activated.
  // NOTE: `/sources` and `/signals` are mounted WITHOUT the /api/v1 prefix
  // (see the comment in api/emailSources.ts and `client.getJson("/sources")`),
  // unlike /api/v1/email/* and /api/v1/approvals/*. A `**/api/v1/sources**`
  // glob never matches, the mailbox never arrives, the Email nav item stays
  // hidden and every test here dies waiting for a Reply button. Match bare —
  // `**/sources**` still covers a prefixed URL if one is ever added.
  page.route("**/sources**", async (route) => {
    if (route.request().method() !== "GET") return route.continue();
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
            last_polled_at: "2026-07-24T00:00:00Z",
            last_signal_at: null,
            polling_active: true,
          },
        ],
        total: 1,
      }),
    });
  });

  // One flagged email with a sender (so the Reply affordance shows). The windowed
  // fetch returns it; the triaged=set fetch is empty (avoids a double-render).
  page.route("**/signals**", async (route) => {
    if (route.request().method() !== "GET") return route.continue();
    const triaged = route.request().url().includes("triaged=set");
    const signals = triaged
      ? []
      : [
          {
            signal_id: SIGNAL_ID,
            source_id: SOURCE_ID,
            title: "Lunch on Friday?",
            from_addr: "bob@example.com",
            from_display: "Bob",
            content_summary: "Are you free Friday?",
            category: "email",
            category_label: "Email",
            importance_score: 0.9,
            created_at: "2026-07-24T00:00:00Z",
            published_at: "2026-07-24T00:00:00Z",
            triage_status: null,
          },
        ];
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ signals, total: signals.length, next_before: null }),
    });
  });

  page.route("**/api/v1/email/reply-draft**", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ draft: DRAFT }),
    });
  });

  page.route("**/api/v1/approvals/proposals", async (route) => {
    if (route.request().method() !== "POST") return route.continue();
    const body = JSON.parse(route.request().postData() || "{}");
    created.push({ action_name: body.action_name });
    const id = nextId++;
    await route.fulfill({
      status: 201,
      contentType: "application/json",
      body: JSON.stringify({
        proposal_id: id,
        status: "pending",
        first_contact: Boolean(opts.firstContact) && body.action_name !== "email_save_draft",
      }),
    });
  });

  page.route(/\/api\/v1\/approvals\/proposals\/\d+\/approve$/, async (route) => {
    const id = Number(route.request().url().match(/proposals\/(\d+)\//)?.[1]);
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ proposal_id: id, status: "approved" }),
    });
  });

  page.route(/\/api\/v1\/approvals\/proposals\/\d+\/execute$/, async (route) => {
    const id = Number(route.request().url().match(/proposals\/(\d+)\//)?.[1]);
    executed.push(id);
    const ok = opts.executeSuccess !== false;
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        success: ok,
        output: ok ? "ok" : "",
        error: ok ? null : "smtp down",
        reverse_token: null,
        code: ok ? null : "smtp_failed",
      }),
    });
  });

  page.route(/\/api\/v1\/approvals\/proposals\/\d+\/cancel$/, async (route) => {
    const id = Number(route.request().url().match(/proposals\/(\d+)\//)?.[1]);
    canceled.push(id);
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ proposal_id: id, status: "rejected" }),
    });
  });

  return {
    created: () => created,
    executed: () => executed,
    canceled: () => canceled,
  };
}

async function openComposer(page: Page) {
  await page.goto("/email");
  await expect(page.getByTestId("email-inbox-view")).toBeVisible({ timeout: 15_000 });
  await page
    .getByTestId(`email-card-${SIGNAL_ID}`)
    // exact: LUM-705 added a "Reply all" button to the same card, and a
    // substring match resolves to both (Playwright strict-mode violation).
    .getByRole("button", { name: "Reply", exact: true })
    .click();
  const dialog = page.getByRole("dialog", { name: "Reply to email" });
  await expect(dialog).toBeVisible();
  return dialog;
}

test.describe("email send UX (LUM-680)", () => {
  test.skip(!hasSmokeCreds, smokeCredsSkipMessage);

  test.beforeEach(async ({ page }) => {
    await loginWithSmokeCredentials(page);
  });

  test("reply → draft populates → confirm → Send → Sent ✓", async ({ page }) => {
    const rec = installMocks(page, { firstContact: false });
    const dialog = await openComposer(page);
    // The on-box draft populates the (untouched) body.
    await expect(dialog.getByLabel("Message")).toHaveValue(DRAFT, { timeout: 10_000 });

    await dialog.getByRole("button", { name: "Review" }).click();
    await expect(dialog.getByText(/Send this to/)).toContainText("bob@example.com");
    await dialog.getByRole("button", { name: "Send" }).click();

    await expect(dialog.getByText("Sent ✓")).toBeVisible();
    expect(rec.created().some((c) => c.action_name === "email_reply")).toBe(true);
    expect(rec.executed().length).toBe(1);
  });

  test("first-contact banner shows for a never-emailed recipient", async ({ page }) => {
    installMocks(page, { firstContact: true });
    const dialog = await openComposer(page);
    await dialog.getByRole("button", { name: "Review" }).click();
    // LUM-698 made this copy deliberately recipient-AGNOSTIC: the flag can be
    // raised by any recipient including a cc/bcc, so naming `to` misattributed
    // it, and `_annotate_send` returns only a boolean by design — naming the
    // real address would mean weakening that contract. Asserting the old
    // "haven't emailed bob@example.com before" wording tested the bug.
    const banner = dialog.getByRole("note");
    await expect(banner).toHaveText(/one or more recipients haven.t been emailed/i);
    // Pin the property, not just the string. Scoped to the banner, NOT the whole
    // dialog — the address appears legitimately in the To field.
    await expect(banner).not.toHaveText(/bob@example\.com/);
  });

  test("Save to Drafts → 'Saved to Drafts ✓' (never 'Sent'), cancels the send proposal", async ({
    page,
  }) => {
    const rec = installMocks(page);
    const dialog = await openComposer(page);
    await expect(dialog.getByLabel("Message")).toHaveValue(DRAFT, { timeout: 10_000 });
    await dialog.getByRole("button", { name: "Review" }).click();
    await dialog.getByRole("button", { name: "Save to Drafts" }).click();

    await expect(dialog.getByText("Saved to Drafts ✓")).toBeVisible();
    await expect(dialog.getByText("Sent ✓")).toHaveCount(0);
    expect(rec.created().some((c) => c.action_name === "email_save_draft")).toBe(true);
    // The review-created send proposal is abandoned, not left pending (LUM-682).
    expect(rec.canceled().length).toBeGreaterThanOrEqual(1);
  });

  test("failed send preserves the text and offers Retry", async ({ page }) => {
    installMocks(page, { executeSuccess: false });
    const dialog = await openComposer(page);
    await expect(dialog.getByLabel("Message")).toHaveValue(DRAFT, { timeout: 10_000 });
    await dialog.getByRole("button", { name: "Review" }).click();
    await dialog.getByRole("button", { name: "Send" }).click();

    await expect(dialog.getByRole("button", { name: "Retry" })).toBeVisible();
    // The composed text survives the failure (Back returns to a populated editor).
    await dialog.getByRole("button", { name: "Back" }).click();
    await expect(dialog.getByLabel("Message")).toHaveValue(DRAFT);
  });
});
