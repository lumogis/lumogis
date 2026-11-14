// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-736 — e2e for the LUM-696 bulk triage surface: multi-select, the bulk
// action bar, single-key shortcuts, and undo.
//
// Hybrid: smoke login + page.route mocks, same pattern as email_send.spec.ts.
//
// WHY A BROWSER. `selection.ts` and `useTriageKeyboard.ts` ship with Vitest
// coverage of the reducer and the switch. What jsdom cannot prove is that a
// keypress reaches the handler at all: the hook bails on `isTypingTarget` and
// on meta/ctrl/alt, and the cursor is anchored to a real `.focus()` on the row
// checkbox so the visual cursor cannot drift from actual focus. Those are
// browser facts.
//
// THE REQUEST-SHAPE ASSERTION IS THE LOAD-BEARING ONE. Bulk triage must send
// ONE request carrying every id, not one per row. A per-row implementation
// looks identical on screen and passes any assertion about rendered state — it
// only shows up as N times the requests, so the count is asserted directly.
//
// NOTE ON ROUTE GLOBS: /signals and /signals/triage/bulk are mounted WITHOUT
// the /api/v1 prefix. The bulk route is registered AFTER the list route because
// Playwright matches most-recently-registered first and `**/signals**` would
// otherwise swallow it.

import { test, expect, type Page } from "@playwright/test";

import {
  hasSmokeCreds,
  loginWithSmokeCredentials,
  smokeCredsSkipMessage,
} from "./smoke-auth";

const SOURCE_ID = "11111111-1111-1111-1111-111111111111";
const IDS = ["sig-e2e-bulk-1", "sig-e2e-bulk-2", "sig-e2e-bulk-3"];

interface BulkCall {
  signal_ids: string[];
  status: string | null;
}

interface Recorder {
  calls: () => BulkCall[];
}

function installMocks(page: Page, opts: { skipped?: string[] } = {}): Recorder {
  const calls: BulkCall[] = [];

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
    const rows = triaged
      ? []
      : IDS.map((id, i) => ({
          signal_id: id,
          source_id: SOURCE_ID,
          title: `Bulk subject ${i + 1}`,
          from_addr: `sender${i + 1}@example.com`,
          from_display: `Sender ${i + 1}`,
          content_summary: "Body.",
          category: "email",
          category_label: "Email",
          importance_score: 0.9,
          created_at: "2026-07-28T09:00:00Z",
          published_at: "2026-07-28T09:00:00Z",
          triage_status: null,
        }));
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ signals: rows, total: rows.length, next_before: null }),
    });
  });

  // Registered after `**/signals**` so it wins the match.
  page.route("**/signals/triage/bulk", async (route) => {
    const body = route.request().postDataJSON() as BulkCall;
    calls.push({ signal_ids: body.signal_ids, status: body.status });
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ updated: body.signal_ids.length, skipped: opts.skipped ?? [] }),
    });
  });

  return { calls: () => calls };
}

async function openEmail(page: Page) {
  await page.goto("/email");
  await expect(page.getByTestId("email-inbox-view")).toBeVisible();
  await expect(page.getByTestId(`email-card-${IDS[0]}`)).toBeVisible();
}

function rowCheckbox(page: Page, id: string) {
  return page.locator(`[data-triage-checkbox="${id}"]`);
}

test.describe("email bulk triage (LUM-736)", () => {
  test.skip(!hasSmokeCreds, smokeCredsSkipMessage);

  test.beforeEach(async ({ page }) => {
    await loginWithSmokeCredentials(page);
  });

  test("a bulk action sends one request carrying every id, not one per row", async ({ page }) => {
    const rec = installMocks(page);
    await openEmail(page);

    for (const id of IDS) await rowCheckbox(page, id).check();
    await expect(page.getByTestId("bulk-action-bar")).toContainText("3 selected");

    await page.getByRole("button", { name: "Mark done" }).click();

    await expect.poll(() => rec.calls().length).toBe(1);
    const [call] = rec.calls();
    expect(call?.signal_ids.slice().sort()).toEqual(IDS.slice().sort());
    expect(call?.status).toBe("done");
  });

  test("select-all in a lane selects its rows, and Clear selection empties the bar", async ({
    page,
  }) => {
    installMocks(page);
    await openEmail(page);

    await page.getByTestId("select-all-needs_you").check();
    await expect(page.getByTestId("bulk-action-bar")).toContainText("3 selected");
    for (const id of IDS) await expect(rowCheckbox(page, id)).toBeChecked();

    await page.getByRole("button", { name: "Clear selection" }).click();
    // The bar unmounts at zero rather than rendering "0 selected".
    await expect(page.getByTestId("bulk-action-bar")).toHaveCount(0);
  });

  test("j moves the cursor to a real focused checkbox, x selects it, e acts on it", async ({
    page,
  }) => {
    const rec = installMocks(page);
    await openEmail(page);

    // The cursor is anchored to .focus() on the row checkbox precisely so it
    // cannot drift from browser focus — assert actual focus, not a CSS class.
    await page.keyboard.press("j");
    await expect(rowCheckbox(page, IDS[0]!)).toBeFocused();

    await page.keyboard.press("x");
    await expect(rowCheckbox(page, IDS[0]!)).toBeChecked();
    await expect(page.getByTestId("bulk-action-bar")).toContainText("1 selected");

    await page.keyboard.press("e");
    await expect.poll(() => rec.calls().length).toBe(1);
    expect(rec.calls()[0]?.signal_ids).toEqual([IDS[0]]);
  });

  test("typing in the filter does not trigger shortcuts", async ({ page }) => {
    // `isTypingTarget` is the guard that stops every letter in a search box
    // triaging mail. jsdom can call the hook; only a browser proves the real
    // keydown path respects it.
    installMocks(page);
    await openEmail(page);

    const filter = page.getByLabel("Filter loaded email");
    await filter.click();
    await filter.fill("exe");

    await expect(page.getByTestId("bulk-action-bar")).toHaveCount(0);
    await expect(filter).toHaveValue("exe");
  });

  test("a 200 that skips rows reports what actually landed", async ({ page }) => {
    // A 200 is not proof of a write: ids the caller can no longer see come back
    // in `skipped`. Claiming all three landed would leave rows showing a status
    // the server never stored, and offer an Undo for a change that never
    // happened.
    installMocks(page, { skipped: [IDS[2]!] });
    await openEmail(page);

    await page.getByTestId("select-all-needs_you").check();
    await page.getByRole("button", { name: "Mark done" }).click();

    await expect(page.getByText(/updated 2 of 3 — 1 no longer available/i)).toBeVisible();
  });

  test("undo reverses a bulk action", async ({ page }) => {
    const rec = installMocks(page);
    await openEmail(page);

    await page.getByTestId("select-all-needs_you").check();
    await page.getByRole("button", { name: "Mark done" }).click();

    const undo = page.getByTestId("undo-banner");
    await expect(undo).toContainText("3 updated");
    await undo.getByRole("button", { name: "Undo" }).click();

    // The reversal is a second call that clears the status it just set —
    // `status: null` is what "back to untriaged" looks like on the wire.
    await expect.poll(() => rec.calls().length).toBe(2);
    expect(rec.calls()[1]?.status).toBeNull();
    expect(rec.calls()[1]?.signal_ids.slice().sort()).toEqual(IDS.slice().sort());
  });
});
