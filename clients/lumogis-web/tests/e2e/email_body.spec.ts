// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-810 — e2e for the read view.
//
// Hybrid: smoke login + page.route mocks, same pattern as email_attachments.spec.ts.
// No live mailbox is needed — only a running web app and a logged-in session.
// Creds-gated: skips cleanly without LUMOGIS_WEB_SMOKE_* (`make web-e2e-prove`).
//
// What this layer adds over the Vitest suite: the route answers **200 with a
// reason** for every expected failure rather than an HTTP error, so the thing
// that can silently regress is whether those reasons reach the reader as
// distinguishable sentences. jsdom proves the component maps reason → copy; only
// a browser proves the mapped copy actually renders on the card, in the page the
// user is looking at, at the width they hold.
//
// NOTE ON ROUTE GLOBS: `/sources` and `/signals` are mounted WITHOUT the
// /api/v1 prefix (see api/emailSources.ts), while /api/v1/email/* is prefixed.
// Matching `**/api/v1/sources**` silently never fires and every test here dies
// waiting for a card — the trap documented in email_attachments.spec.ts.

import { test, expect, type Page } from "@playwright/test";

import { hasSmokeCreds, loginWithSmokeCredentials, smokeCredsSkipMessage } from "./smoke-auth";

const SOURCE_ID = "11111111-1111-1111-1111-111111111111";
const SIGNAL_ID = "sig-e2e-body-1";

interface BodyOpts {
  body?: string;
  truncated?: boolean;
  degraded?: boolean;
  reason?: string;
  /** A signal whose source_id is NOT owned — the household-shared case. */
  foreignSource?: boolean;
  /** LUM-713 — inline images ride this same response. */
  inlineImages?: { content_id: string; mime_type: string; size_bytes: number; data_uri: string }[];
  inlineImagesTruncated?: boolean;
}

// Real PNG bytes, not placeholders: the whole point of this layer is that a
// browser decodes what the server sniffed. A stub string would render as a
// broken image and the tests below would still pass on `toBeVisible`.
const SMALL_PNG =
  "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAABgAAAAYCAIAAABvFaqvAAAAH0lEQVR42mM4YWNDFcQwatCoQaMGjRo0atCoQQNvEACSR9AfycJoDQAAAABJRU5ErkJggg==";
// 1200px wide — wider than a phone. The strip must contain it, not push the
// page sideways, which is the property the mobile test exists to prove.
const WIDE_PNG =
  "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAABLAAAAAoCAIAAABhOZSlAAAA4klEQVR42u3XMQ0AAAgEsVeCTiQiCxVMNKmC2y7VAwAAwEORAAAAwBACAABgCAEAADCEAAAAGEIAAAAMIQAAAIYQAAAAQwgAAIAhBAAAwBACAABgCAEAADCEAAAAGEIAAAAMIQAAAIYQAAAAQwgAAIAhBAAAwBACAABgCAEAADCEAAAAGEIAAAAMIQAAAIYQAAAAQwgAAGAIAQAAMIQAAAAYQgAAAAwhAAAAhhAAAABDCAAAgCEEAADAEAIAAGAIAQAAMIQAAAAYQgAAAAwhAAAAhhAAAABDCAAAgCEEAADgxgJ7MmgB+aTU8QAAAABJRU5ErkJggg==";

function inlineImage(cid: string, dataUri: string = SMALL_PNG) {
  return { content_id: cid, mime_type: "image/png", size_bytes: 88, data_uri: dataUri };
}

function installMocks(page: Page, opts: BodyOpts = {}): { bodyCalls: () => number } {
  let bodyCalls = 0;

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
                // A source_id the /sources fetch does not return models
                // another member's mailbox: the read affordance must not appear.
                source_id: opts.foreignSource ? "99999999-9999-9999-9999-999999999999" : SOURCE_ID,
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

  page.route("**/api/v1/email/body", async (route) => {
    bodyCalls += 1;
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        body: opts.body ?? "Term 2 fees are due 30 April.\n\nPlease pay via the parent portal.",
        truncated: opts.truncated ?? false,
        degraded: opts.degraded ?? false,
        reason: opts.reason ?? "ok",
        inline_images: opts.inlineImages ?? [],
        inline_images_truncated: opts.inlineImagesTruncated ?? false,
      }),
    });
  });

  return { bodyCalls: () => bodyCalls };
}

async function openBody(page: Page) {
  await page.goto("/email");
  const card = page.getByTestId(`email-card-${SIGNAL_ID}`);
  await expect(card).toBeVisible();
  // Lazy by design: each body is a live IMAP fetch, so it fires on first open
  // of the disclosure, never on render of a list of cards.
  await page.getByTestId(`body-${SIGNAL_ID}`).getByText("Message", { exact: true }).click();
  return card;
}

test.describe("email body read view (LUM-810)", () => {
  test.skip(!hasSmokeCreds, smokeCredsSkipMessage);

  test.beforeEach(async ({ page }) => {
    await loginWithSmokeCredentials(page);
  });

  test("the message text is readable, and the text-only limit is stated", async ({ page }) => {
    installMocks(page);
    await openBody(page);

    await expect(page.getByText("Please pay via the parent portal.")).toBeVisible();
    // The ADR requires this be STATED, not discovered: a newsletter reading as
    // flattened text should not look like a rendering bug.
    await expect(page.getByText(/Showing text only/)).toBeVisible();
  });

  test("the body is not fetched until the disclosure is opened", async ({ page }) => {
    const { bodyCalls } = installMocks(page);
    await page.goto("/email");
    await expect(page.getByTestId(`email-card-${SIGNAL_ID}`)).toBeVisible();

    expect(bodyCalls()).toBe(0);

    await page.getByTestId(`body-${SIGNAL_ID}`).getByText("Message", { exact: true }).click();
    await expect(page.getByText(/Showing text only/)).toBeVisible();
    expect(bodyCalls()).toBe(1);
  });

  test("a long message truncates and expands", async ({ page }) => {
    installMocks(page, { body: "x".repeat(600) });
    await openBody(page);

    await page.getByRole("button", { name: "Show all" }).click();
    await expect(page.getByRole("button", { name: "Show less" })).toBeVisible();
  });

  test("a server-truncated message says so rather than looking complete", async ({ page }) => {
    installMocks(page, { body: "The first part only.", truncated: true });
    await openBody(page);

    await expect(page.getByText(/Message truncated/)).toBeVisible();
  });

  test("mailbox unreachable reads as retryable, and offers the retry", async ({ page }) => {
    installMocks(page, { body: "", degraded: true, reason: "mailbox_unreachable" });
    await openBody(page);

    await expect(page.getByText(/Mailbox unreachable/)).toBeVisible();
    await expect(page.getByRole("button", { name: "Try again" })).toBeVisible();
  });

  test("a deleted message reads as terminal, and offers no retry", async ({ page }) => {
    // The 503/410 distinction the attachment surface makes, carried over: a
    // message that is gone must not invite a retry that can never succeed.
    installMocks(page, { body: "", reason: "not_found" });
    await openBody(page);

    await expect(page.getByText(/no longer in your mailbox/)).toBeVisible();
    await expect(page.getByRole("button", { name: "Try again" })).toHaveCount(0);
  });

  test("an oversize message points at the mail app instead of failing silently", async ({
    page,
  }) => {
    installMocks(page, { body: "", reason: "too_large" });
    await openBody(page);

    await expect(page.getByText(/too large to show here/)).toBeVisible();
  });

  test("a message's inline images appear below the text, and actually decode", async ({ page }) => {
    // LUM-713. `toBeVisible` passes on a broken image, so this asserts
    // `naturalWidth` — the browser's own statement that it decoded the bytes.
    installMocks(page, { inlineImages: [inlineImage("<logo1>"), inlineImage("<logo2>")] });
    await openBody(page);

    const strip = page.getByTestId("inline-image-strip");
    await expect(strip).toBeVisible();
    await expect(strip.locator("img")).toHaveCount(2);
    for (const img of await strip.locator("img").all()) {
      await expect
        .poll(() => img.evaluate((el: HTMLImageElement) => el.naturalWidth))
        .toBeGreaterThan(0);
    }
  });

  test("the text-only footer stops claiming images are hidden once they are shown", async ({
    page,
  }) => {
    installMocks(page, { inlineImages: [inlineImage("<logo1>")] });
    await openBody(page);

    await expect(page.getByText(/images and formatting are not displayed/)).toHaveCount(0);
    await expect(page.getByText(/formatting is not displayed/)).toBeVisible();
  });

  test('an image-only message shows its images, not just "no text"', async ({ page }) => {
    // A scanned letter: the server answers `no_body` and a full strip. The
    // reason branch used to return before the strip, so the headline feature
    // did nothing on exactly the messages that most need it.
    installMocks(page, { body: "", reason: "no_body", inlineImages: [inlineImage("<scan>")] });
    await openBody(page);

    await expect(page.getByText(/No message text available/)).toBeVisible();
    const img = page.getByTestId("inline-image-strip").locator("img").first();
    await expect
      .poll(() => img.evaluate((el: HTMLImageElement) => el.naturalWidth))
      .toBeGreaterThan(0);
  });

  test("no strip at all for a message with no inline images", async ({ page }) => {
    installMocks(page);
    await openBody(page);

    await expect(page.getByText(/Showing text only/)).toBeVisible();
    await expect(page.getByTestId("inline-image-strip")).toHaveCount(0);
  });

  test("a capped strip says images are missing rather than looking complete", async ({ page }) => {
    installMocks(page, { inlineImages: [inlineImage("<a>")], inlineImagesTruncated: true });
    await openBody(page);

    await expect(page.getByText(/weren’t shown/)).toBeVisible();
  });

  test("another member's mail explains itself instead of offering a dead affordance", async ({
    page,
  }) => {
    // LUM-719: a non-owner can never fetch this body. Offering the disclosure
    // and letting it 404 was filed as a bug once already.
    const { bodyCalls } = installMocks(page, { foreignSource: true });
    await page.goto("/email");
    await expect(page.getByTestId(`email-card-${SIGNAL_ID}`)).toBeVisible();

    await expect(page.getByTestId(`body-${SIGNAL_ID}`)).toHaveCount(0);
    await expect(page.getByText(/only they can open this message/)).toBeVisible();
    // Decided client-side from ownership — never by probing the route.
    expect(bodyCalls()).toBe(0);
  });
});

test.describe("email body read view — mobile (LUM-810)", () => {
  test.use({ viewport: { width: 390, height: 844 } });

  test.skip(!hasSmokeCreds, smokeCredsSkipMessage);

  test.beforeEach(async ({ page }) => {
    await loginWithSmokeCredentials(page);
  });

  test("reads on a phone without horizontal overflow", async ({ page }) => {
    // The surface is mobile-first, and extracted text carries tabs between
    // table cells — a receipt is exactly the shape that would push the page
    // sideways if the container did not handle it.
    installMocks(page, {
      body: "Trail Runner Pro\t£129.00\nShipping\t£4.99\n\nThanks for your order.",
    });
    await openBody(page);

    await expect(page.getByText(/Thanks for your order/)).toBeVisible();

    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth > document.documentElement.clientWidth,
    );
    expect(overflow).toBe(false);
  });

  test("a 1200px inline image is contained, not allowed to push the page sideways", async ({
    page,
  }) => {
    // A sender picks the pixel dimensions, so this is sender-controlled layout:
    // a banner three times the viewport width is ordinary marketing mail, and
    // the strip has to bound it (LUM-713).
    installMocks(page, { inlineImages: [inlineImage("<banner>", WIDE_PNG)] });
    await openBody(page);

    const img = page.getByTestId("inline-image-strip").locator("img").first();
    await expect(img).toBeVisible();
    await expect.poll(() => img.evaluate((el: HTMLImageElement) => el.naturalWidth)).toBe(1200);

    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth > document.documentElement.clientWidth,
    );
    expect(overflow).toBe(false);
  });
});
