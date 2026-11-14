// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-734 — e2e for the LUM-695/702/727 attachment surface.
//
// Hybrid: smoke login + page.route mocks, same pattern as email_send.spec.ts.
// No live mailbox is needed — only a running web app and a logged-in session.
// Creds-gated: skips cleanly without LUMOGIS_WEB_SMOKE_* (`make web-e2e-prove`).
//
// What this layer adds over the Vitest suite: attachment bytes are never stored,
// so every open is an on-demand IMAP refetch and the interesting failures are
// HTTP-shaped. 503 (mailbox unreachable, retryable) and 410 (message deleted
// upstream, never) MUST read differently to the user — collapsing them makes a
// deliberate product trade look like a bug. jsdom can assert the mapping
// function; only a browser proves the rendered result.
//
// NOTE ON ROUTE GLOBS: `/sources` and `/signals` are mounted WITHOUT the
// /api/v1 prefix (see api/emailSources.ts), while /api/v1/email/* is prefixed.
// Matching `**/api/v1/sources**` silently never fires, leaves the Email nav
// item hidden, and every test here dies waiting for a card. That cost
// email_send.spec.ts four tests and two hours.

import { test, expect, type Page } from "@playwright/test";

import {
  hasSmokeCreds,
  loginWithSmokeCredentials,
  smokeCredsSkipMessage,
} from "./smoke-auth";

const SOURCE_ID = "11111111-1111-1111-1111-111111111111";
const SIGNAL_ID = "sig-e2e-att-1";

interface AttachmentOpts {
  /** Status for GET .../attachments/:part — the download. 200 unless set. */
  downloadStatus?: number;
  /** false models a metadata-mode mailbox: "cannot know", not "none". */
  available?: boolean;
  rows?: Array<{
    part_index: number;
    filename: string;
    mime_type: string;
    size_bytes: number;
  }>;
}

const DEFAULT_ROWS = [
  {
    part_index: 2,
    filename: "invoice.pdf",
    mime_type: "application/pdf",
    size_bytes: 245_760,
  },
  {
    part_index: 3,
    filename: "permission-slip.pdf",
    mime_type: "application/pdf",
    size_bytes: 51_200,
  },
];

function installMocks(page: Page, opts: AttachmentOpts = {}): { filed: () => number[] } {
  const filed: number[] = [];
  const rows = opts.rows ?? DEFAULT_ROWS;
  const available = opts.available ?? true;

  // One imap mailbox, so `isEmailActivated` is true and the Email nav renders.
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
            url: "imap://imap.example.com:993/personal",
            category: "email",
            active: true,
            poll_interval: 900,
            last_polled_at: "2026-07-28T00:00:00Z",
            last_signal_at: null,
            polling_active: true,
          },
        ],
        total: 1,
      }),
    });
  });

  // One flagged email. The triaged=set fetch is empty to avoid a double render.
  page.route("**/signals**", async (route) => {
    if (route.request().method() !== "GET") return route.continue();
    const triaged = route.request().url().includes("triaged=set");
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        signals: triaged
          ? []
          : [
              {
                signal_id: SIGNAL_ID,
                source_id: SOURCE_ID,
                title: "Term 2 invoice",
                from_addr: "office@school.example.com",
                from_display: "Fairview Primary",
                content_summary: "Term 2 fees are due 30 April.",
                category: "email",
                category_label: "Email",
                importance_score: 0.9,
                created_at: "2026-07-28T09:00:00Z",
                url: `imap://imap.example.com:993/personal/${SIGNAL_ID}`,
                triage_state: null,
              },
            ],
        total: triaged ? 0 : 1,
        next_cursor: null,
      }),
    });
  });

  // Attachment metadata for the card. `available: false` is NOT an empty list.
  page.route(`**/api/v1/email/signals/${SIGNAL_ID}/attachments`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ rows: available ? rows : [], available }),
    });
  });

  // The download itself — where 503 and 410 have to stay distinguishable.
  page.route(/\/api\/v1\/email\/signals\/[^/]+\/attachments\/\d+$/, async (route) => {
    const status = opts.downloadStatus ?? 200;
    if (status === 200) {
      return route.fulfill({
        status: 200,
        contentType: "application/pdf",
        headers: { "Content-Disposition": 'attachment; filename="invoice.pdf"' },
        body: "%PDF-1.4 fake",
      });
    }
    await route.fulfill({
      status,
      contentType: "application/json",
      body: JSON.stringify({ detail: `mocked ${status}` }),
    });
  });

  // LUM-727: filing is per-attachment and never automatic.
  page.route("**/api/v1/email/attachment-ingest/candidates/accept", async (route) => {
    const body = route.request().postDataJSON() as { part_index?: number };
    if (typeof body?.part_index === "number") filed.push(body.part_index);
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ filed: 1, document_id: "doc-1" }),
    });
  });

  return { filed: () => filed };
}

async function openAttachments(page: Page) {
  await page.goto("/email");
  const card = page.getByTestId(`email-card-${SIGNAL_ID}`);
  await expect(card).toBeVisible();
  // Lazy by design (LUM-695): the fetch fires on first open, not on render.
  await page.getByTestId(`attachments-${SIGNAL_ID}`).getByText("Attachments").click();
  return card;
}

test.describe("email attachments (LUM-734)", () => {
  test.skip(!hasSmokeCreds, smokeCredsSkipMessage);

  test.beforeEach(async ({ page }) => {
    await loginWithSmokeCredentials(page);
  });

  test("attachment list renders filename and a human size, never a raw byte count", async ({
    page,
  }) => {
    installMocks(page);
    await openAttachments(page);

    await expect(page.getByRole("button", { name: "invoice.pdf" })).toBeVisible();
    await expect(page.getByRole("button", { name: "permission-slip.pdf" })).toBeVisible();
    // 245760 rendered verbatim would be the "three renderers stop showing
    // integers" regression LUM-724 fixed.
    await expect(page.getByText("245760")).toHaveCount(0);
  });

  test("a metadata-mode mailbox says it cannot know, rather than showing an empty list", async ({
    page,
  }) => {
    // `available: false` means the mailbox never fetched bodies. Rendering
    // "no attachments" for a message that has three is an outright lie, and
    // the API comment calls that out explicitly.
    installMocks(page, { available: false });
    await openAttachments(page);

    await expect(page.getByTestId("attachments-unavailable")).toBeVisible();
  });

  test("mailbox unreachable (503) reads as retryable", async ({ page }) => {
    installMocks(page, { downloadStatus: 503 });
    await openAttachments(page);
    await page.getByRole("button", { name: "invoice.pdf" }).click();

    const err = page.getByRole("alert");
    await expect(err).toHaveText(/can.t reach the mailbox/i);
    // The distinction that matters: this must NOT read as permanent.
    await expect(err).not.toHaveText(/no longer in the mailbox/i);
  });

  test("message gone upstream (410) reads as permanent, not as a retry", async ({ page }) => {
    installMocks(page, { downloadStatus: 410 });
    await openAttachments(page);
    await page.getByRole("button", { name: "invoice.pdf" }).click();

    const err = page.getByRole("alert");
    await expect(err).toHaveText(/no longer in the mailbox/i);
    // Telling the user to try again for a message that is gone forever is the
    // failure this pair of assertions exists to prevent.
    await expect(err).not.toHaveText(/try again/i);
  });

  test("410 and 503 do not render the same message", async ({ page }) => {
    // Guards the collapse directly: if someone maps both onto one generic
    // string, every other assertion here still passes individually.
    installMocks(page, { downloadStatus: 503 });
    await openAttachments(page);
    await page.getByRole("button", { name: "invoice.pdf" }).click();
    const unreachable = await page.getByRole("alert").textContent();

    await page.unrouteAll();
    installMocks(page, { downloadStatus: 410 });
    await openAttachments(page);
    await page.getByRole("button", { name: "invoice.pdf" }).click();
    const gone = await page.getByRole("alert").textContent();

    expect(unreachable?.trim()).not.toBe(gone?.trim());
  });
});
