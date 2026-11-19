// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-738 — e2e for the LUM-697 unsubscribe / subscriptions surface.
//
// Hybrid: smoke login + page.route mocks, same pattern as email_cleanup.spec.ts.
// The view lives under Settings → Email signals → per-mailbox "Clean up this
// mailbox" (LUM-742 routed it here; it is sender-grouped, not category-grouped).
//
// WHAT THE BROWSER ADDS OVER VITEST. `isSafeUnsubscribeUrl` is pure and already
// unit-tested; the load-bearing browser proof is that a stored `javascript:` URI
// never becomes a live `<a href>` in the DOM (CVE-2025-68673). jsdom can assert
// React did not render the link node; only Playwright proves the browser would
// not navigate if something else injected it.
//
// THE REQUEST-SHAPE ASSERTION IS LOAD-BEARING. Batch unsubscribe must POST ONE
// proposal carrying every selected sender — never one request per row. A per-row
// implementation looks identical on screen and only shows up as duplicate POSTs.

import { test, expect, type Page } from "@playwright/test";

import {
  hasSmokeCreds,
  loginWithSmokeCredentials,
  smokeCredsSkipMessage,
} from "./smoke-auth";

const SOURCE_ID = "11111111-1111-1111-1111-111111111111";

interface SubscriptionRow {
  source_id: string;
  from_addr: string;
  from_display: string;
  message_count: number;
  has_mailto: boolean;
  has_https: boolean;
  one_click: boolean;
  https_uri: string;
  last_seen_at: string | null;
  unsubscribed_at: string | null;
  suppressed_as: string | null;
}

function row(over: Partial<SubscriptionRow> = {}): SubscriptionRow {
  return {
    source_id: SOURCE_ID,
    from_addr: "news@retailer.example",
    from_display: "Retailer",
    message_count: 41,
    has_mailto: true,
    has_https: false,
    one_click: false,
    https_uri: "",
    last_seen_at: null,
    unsubscribed_at: null,
    suppressed_as: null,
    ...over,
  };
}

interface ProposeCall {
  source_id: string;
  senders: string[];
}

interface Recorder {
  proposeCalls: () => ProposeCall[];
  hiddenAddrs: () => string[];
}

function installMocks(
  page: Page,
  opts: {
    rows?: SubscriptionRow[];
    onHide?: (addr: string) => void;
  } = {},
): Recorder {
  const proposeCalls: ProposeCall[] = [];
  const hiddenAddrs: string[] = [];
  let rows = opts.rows ?? [row()];

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
            last_polled_at: null,
            last_signal_at: null,
            polling_active: true,
          },
        ],
        total: 1,
      }),
    });
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
          {
            slug: "promotional_bulk",
            label: "Promotional / bulk",
            enabled: true,
            is_custom: false,
            is_protected: false,
            sort: 0,
          },
          {
            slug: "other",
            label: "Other",
            enabled: true,
            is_custom: false,
            is_protected: true,
            sort: 1,
          },
        ],
      }),
    });
  });

  page.route("**/api/v1/me/email-classification-rules**", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ rules: [] }),
    });
  });

  page.route("**/signals**", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ signals: [], total: 0 }),
    });
  });

  page.route(`**/me/email-sources/${SOURCE_ID}/cleanup-spam`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ spam_count: 0, trash_available: false }),
    });
  });

  page.route("**/api/v1/email/subscriptions**", async (route) => {
    if (route.request().method() !== "GET") return route.continue();
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ rows, truncated: false }),
    });
  });

  page.route("**/api/v1/email/unsubscribe/propose", async (route) => {
    const body = route.request().postDataJSON() as ProposeCall;
    proposeCalls.push(body);
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        proposal_id: 7,
        targets: body.senders.map((addr) => ({
          from_addr: addr,
          mailto_addr: `unsub@${addr.split("@")[1] ?? "example.com"}`,
          subject: "unsubscribe",
        })),
        skipped: [],
      }),
    });
  });

  page.route("**/api/v1/email/sender-rules", async (route) => {
    if (route.request().method() === "POST") {
      const body = route.request().postDataJSON() as { from_addr: string };
      hiddenAddrs.push(body.from_addr);
      rows = rows.map((r) =>
        r.from_addr === body.from_addr ? { ...r, suppressed_as: "hide" } : r,
      );
      opts.onHide?.(body.from_addr);
      await route.fulfill({ status: 200, contentType: "application/json", body: "{}" });
      return;
    }
    if (route.request().method() === "DELETE") {
      const url = new URL(route.request().url());
      const addr = url.searchParams.get("from_addr") ?? "";
      rows = rows.map((r) =>
        r.from_addr === addr ? { ...r, suppressed_as: null } : r,
      );
      await route.fulfill({ status: 200, contentType: "application/json", body: "{}" });
      return;
    }
    await route.continue();
  });

  return {
    proposeCalls: () => proposeCalls,
    hiddenAddrs: () => hiddenAddrs,
  };
}

async function openSubscriptionsPanel(page: Page) {
  await page.goto("/settings/email");
  await expect(page.getByTestId("email-signals-view")).toBeVisible({ timeout: 15_000 });
  await page.getByText("Clean up this mailbox").click();
  await expect(page.getByLabel("Subscriptions")).toBeVisible();
}

test.describe("email subscriptions (LUM-738)", () => {
  test.skip(!hasSmokeCreds, smokeCredsSkipMessage);

  test.beforeEach(async ({ page }) => {
    await loginWithSmokeCredentials(page);
  });

  test("lists senders with counts under Clean up this mailbox", async ({ page }) => {
    installMocks(page, {
      rows: [
        row(),
        row({ from_addr: "digest@health.example", from_display: "Health digest", message_count: 12 }),
      ],
    });
    await openSubscriptionsPanel(page);
    await expect(page.getByText("Retailer")).toBeVisible();
    await expect(page.getByText("41 messages")).toBeVisible();
    await expect(page.getByText("Health digest")).toBeVisible();
    await expect(page.getByText("12 messages")).toBeVisible();
  });

  test("safe https URI becomes an external unsubscribe link", async ({ page }) => {
    installMocks(page, {
      rows: [row({ has_https: true, https_uri: "https://retailer.example/unsub?t=1" })],
    });
    await openSubscriptionsPanel(page);
    const link = page.getByTestId("unsubscribe-link");
    await expect(link).toBeVisible();
    await expect(link).toHaveAttribute("href", "https://retailer.example/unsub?t=1");
    await expect(link).toHaveAttribute("target", "_blank");
    const rel = await link.getAttribute("rel");
    expect(rel).toContain("noopener");
    expect(rel).toContain("noreferrer");
  });

  test("stored javascript: URI is not rendered as a clickable link", async ({ page }) => {
    installMocks(page, {
      rows: [row({ has_https: true, https_uri: "javascript:alert(1)" })],
    });
    await openSubscriptionsPanel(page);
    await expect(page.getByTestId("subscription-row")).toBeVisible();
    await expect(page.getByTestId("unsubscribe-link")).toHaveCount(0);
  });

  test("batch unsubscribe sends ONE propose request with senders only", async ({ page }) => {
    const rec = installMocks(page, {
      rows: [
        row({ from_addr: "a@x.com", from_display: "A" }),
        row({ from_addr: "b@x.com", from_display: "B" }),
      ],
    });
    await openSubscriptionsPanel(page);
    await page.getByRole("checkbox", { name: "Select a@x.com" }).check();
    await page.getByRole("checkbox", { name: "Select b@x.com" }).check();
    await page.getByRole("button", { name: /Unsubscribe from 2 selected/ }).click();

    await expect(page.getByText(/for approval/)).toBeVisible();
    expect(rec.proposeCalls()).toHaveLength(1);
    expect(rec.proposeCalls()[0]).toEqual({
      source_id: SOURCE_ID,
      senders: ["a@x.com", "b@x.com"],
    });
    expect(JSON.stringify(rec.proposeCalls()[0])).not.toContain("mailto");
  });

  test("Hide future mail flips to Stop hiding after the rule is set", async ({ page }) => {
    const rec = installMocks(page);
    await openSubscriptionsPanel(page);
    await page.getByRole("button", { name: "Hide future mail" }).click();
    await expect(page.getByRole("button", { name: "Stop hiding" })).toBeVisible();
    expect(rec.hiddenAddrs()).toEqual(["news@retailer.example"]);
    await expect(page.getByRole("button", { name: /restore/i })).toHaveCount(0);
  });
});
