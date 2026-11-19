// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-735 — e2e for the LUM-729 external-consumer (paperless starvation)
// consent gate, at the three arming points the web client can actually reach.
//
// Hybrid: smoke login + page.route mocks, same setup as email_organize.spec.ts.
//
// WHAT THE BROWSER CAN AND CANNOT PROVE HERE. Whether the gate SHOULD fire is
// `has_folder_sorting_mailbox` — a SQL predicate that deliberately fails open
// (catches, logs WARNING, returns False) so a database hiccup never locks a
// user out of their own settings. The corollary the ticket calls out is that a
// wrong query silently passes everything. No browser test can see that: with
// mocked routes the refusal is whatever the fixture says. That predicate is
// covered in orchestrator/tests/test_external_consumer_guard.py and
// test_organize_starvation_gate.py.
//
// What a browser CAN prove, and what this file pins:
//
//   * The refusal is recognised by CODE, not by prose. `starvationWarning`
//     matches on `external_consumer_may_starve` precisely so the copy can be
//     reworded without a contract break. Two tests hold that down from both
//     sides: an unfamiliar message still raises the dialog, and a 422 WITHOUT
//     the code does not.
//   * The acknowledgement round-trip — the second request carries
//     `acknowledge_external_consumer: true`, and the first one does not. That
//     omission is what makes the gate mean anything.
//   * Editing any toggle CLEARS the warning, so an acknowledgement can never be
//     carried across a request the user did not see.
//
// STATUS CODE: 422, not 409. The ticket says 409; external_consumer_guard.py
// says otherwise in a comment — "422 rather than 409 at both call sites, to
// match LUM-702's full_ingest_required precondition gate — this is a
// precondition on the request, not a conflict with server state." The client
// only recognises 422 (externalConsumerWarning.ts), so a 409 fixture would
// render a generic error and this whole file would be testing nothing.
//
// THE FOURTH ARM IS NOT HERE. ARM_CONNECT_PAPERLESS — connecting paperless
// while a mailbox is already folder-sorting — fires on POST /sources
// (routes/signals.py). This web client has no paperless-connect surface: no
// component calls that route, and `starvationWarning` is imported by exactly
// two files, neither of them a connector form. There is nothing to drive from
// a browser. It is covered server-side in test_external_consumer_guard.py.

import { test, expect, type Page } from "@playwright/test";

import {
  hasSmokeCreds,
  loginWithSmokeCredentials,
  smokeCredsSkipMessage,
} from "./smoke-auth";

const SOURCE_ID = "11111111-1111-1111-1111-111111111111";
const STARVATION_CODE = "external_consumer_may_starve";

// Deliberately NOT the shipped copy. If the UI ever branched on prose instead
// of the code, these strings would stop matching and the dialog would vanish.
const MESSAGE = "FIXTURE: sorting into folders takes mail out of the inbox paperless watches.";
const ALTERNATIVE = "FIXTURE: use labels only.";
const RECOVERY = "FIXTURE: undo moves mail back.";

interface OrganizePatch {
  acknowledge_external_consumer?: boolean;
  apply_to_existing?: boolean;
}

interface Recorder {
  patches: () => OrganizePatch[];
}

/** `refuseTimes` 422s that many times, then accepts — so the acknowledgement
 *  round-trip can be observed as first-refused-then-accepted. */
function installMocks(
  page: Page,
  opts: { refuseTimes?: number; code?: string; cleanupRefuse?: boolean } = {},
): Recorder {
  const patches: OrganizePatch[] = [];
  let refusals = opts.refuseTimes ?? 0;

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

  page.route(`**/me/email-sources/${SOURCE_ID}/organize`, async (route) => {
    if (route.request().method() !== "PATCH") return route.continue();
    const body = (route.request().postDataJSON() ?? {}) as OrganizePatch;
    patches.push(body);
    if (refusals > 0) {
      refusals -= 1;
      // The server sends `detail` as an object; the shared client stringifies
      // the whole body, and externalConsumerWarning parses it back out. The
      // nesting here mirrors that exactly — flatten it and nothing matches.
      await route.fulfill({
        status: 422,
        contentType: "application/json",
        body: JSON.stringify({
          detail: {
            code: opts.code ?? STARVATION_CODE,
            message: MESSAGE,
            alternative: ALTERNATIVE,
            recovery: RECOVERY,
          },
        }),
      });
      return;
    }
    await route.fulfill({
      status: 202,
      contentType: "application/json",
      body: JSON.stringify({ updated: true, apply_to_existing: body.apply_to_existing ?? false }),
    });
  });

  page.route(`**/me/email-sources/${SOURCE_ID}/cleanup-spam`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ spam_count: 0, trash_available: false }),
    });
  });

  // The arm fires on APPROVE, not on start — email_cleanup.py raises only once
  // a window is approved with at least one moving action. Registered before the
  // progress route below; `*` does not cross `/`, so they cannot collide.
  page.route(`**/me/email-sources/${SOURCE_ID}/cleanup/*/approve`, async (route) => {
    if (opts.cleanupRefuse) {
      await route.fulfill({
        status: 422,
        contentType: "application/json",
        body: JSON.stringify({
          detail: {
            code: STARVATION_CODE,
            message: MESSAGE,
            alternative: ALTERNATIVE,
            recovery: RECOVERY,
          },
        }),
      });
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ approved: 1 }),
    });
  });

  // `state: "ready"` with a movable proposal is what renders the approve
  // control at all — a "backfilling" job shows progress and no button.
  page.route(`**/me/email-sources/${SOURCE_ID}/cleanup/*`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        state: "ready",
        kind: "window",
        progress: 1,
        counts: { scanned: 20, classified: 20, labeled: 20, executed: 0 },
        proposals: [
          { category: "promotional_bulk", label: "Promotional / bulk", count: 12, can_move: true },
        ],
      }),
    });
  });

  page.route(`**/me/email-sources/${SOURCE_ID}/cleanup`, async (route) => {
    if (route.request().method() !== "POST") return route.continue();
    await route.fulfill({
      status: 202,
      contentType: "application/json",
      body: JSON.stringify({ job_id: "job-1", state: "backfilling" }),
    });
  });

  return { patches: () => patches };
}

async function openOrganizePanel(page: Page) {
  await page.goto("/settings/email");
  await expect(page.getByTestId("email-signals-view")).toBeVisible({ timeout: 15_000 });
  await page.getByText("Organize this mailbox").click();
  await expect(page.getByRole("heading", { name: "Organize my mailbox" })).toBeVisible();
}

test.describe("organize starvation gate (LUM-735)", () => {
  test.skip(!hasSmokeCreds, smokeCredsSkipMessage);

  test.beforeEach(async ({ page }) => {
    await loginWithSmokeCredentials(page);
  });

  test("arming folder-sort raises the warning, with the server's own copy", async ({ page }) => {
    installMocks(page, { refuseTimes: 1 });
    await openOrganizePanel(page);

    await page.getByRole("checkbox", { name: /Sort my emails into folders/ }).check();
    await page.getByRole("button", { name: "Save" }).click();

    const dialog = page.getByTestId("starvation-warning");
    await expect(dialog).toBeVisible();
    // Rendered from the response, not hardcoded — the copy is explicitly
    // allowed to change without a contract break.
    await expect(dialog).toContainText(MESSAGE);
    await expect(page.getByTestId("starvation-alternative")).toHaveText(ALTERNATIVE);
    await expect(page.getByTestId("starvation-recovery")).toHaveText(RECOVERY);
  });

  test("the first request omits the acknowledgement and the second carries it", async ({
    page,
  }) => {
    // The omission is the gate. A client that always sent
    // acknowledge_external_consumer: true would never be refused and the
    // warning would never appear — and every rendered assertion would still
    // pass, because the dialog simply would not be reached.
    const rec = installMocks(page, { refuseTimes: 1 });
    await openOrganizePanel(page);

    await page.getByRole("checkbox", { name: /Sort my emails into folders/ }).check();
    await page.getByRole("button", { name: "Save" }).click();
    await expect(page.getByTestId("starvation-warning")).toBeVisible();

    expect(rec.patches()).toHaveLength(1);
    expect(rec.patches()[0]?.acknowledge_external_consumer).toBeUndefined();

    await page.getByTestId("starvation-continue").click();

    await expect.poll(() => rec.patches().length).toBe(2);
    expect(rec.patches()[1]?.acknowledge_external_consumer).toBe(true);
    await expect(page.getByTestId("organize-save-msg")).toContainText(/Lumogis will organize/i);
    await expect(page.getByTestId("starvation-warning")).toHaveCount(0);
  });

  test("the retroactive arm warns too, and its acknowledgement round-trips", async ({ page }) => {
    // apply_to_existing widens the sweep across all history — a retroactive
    // bulk move, and the destructive one of the two organize arms.
    const rec = installMocks(page, { refuseTimes: 1 });
    await openOrganizePanel(page);

    await page.getByRole("checkbox", { name: /Sort my emails into folders/ }).check();
    await page.getByRole("checkbox", { name: /Also apply to existing mail/ }).check();
    await page.getByRole("button", { name: "Save" }).click();

    await expect(page.getByTestId("starvation-warning")).toBeVisible();
    await page.getByTestId("starvation-continue").click();

    await expect.poll(() => rec.patches().length).toBe(2);
    expect(rec.patches()[1]?.apply_to_existing).toBe(true);
    expect(rec.patches()[1]?.acknowledge_external_consumer).toBe(true);
  });

  test("editing a toggle after the warning clears it", async ({ page }) => {
    // An acknowledgement is of a SPECIFIC consequence. Carrying it across an
    // edited request would acknowledge something the user never saw.
    installMocks(page, { refuseTimes: 1 });
    await openOrganizePanel(page);

    await page.getByRole("checkbox", { name: /Sort my emails into folders/ }).check();
    await page.getByRole("button", { name: "Save" }).click();
    await expect(page.getByTestId("starvation-warning")).toBeVisible();

    await page.getByRole("checkbox", { name: /Label my emails/ }).check();
    await expect(page.getByTestId("starvation-warning")).toHaveCount(0);
  });

  test("a 422 without the starvation code is not treated as the gate", async ({ page }) => {
    // The other half of code-matching: an ordinary validation 422 must fall
    // through to normal error handling, not raise a consent dialog offering to
    // proceed anyway.
    installMocks(page, { refuseTimes: 1, code: "some_other_validation_error" });
    await openOrganizePanel(page);

    await page.getByRole("checkbox", { name: /Sort my emails into folders/ }).check();
    await page.getByRole("button", { name: "Save" }).click();

    await expect(page.getByTestId("starvation-warning")).toHaveCount(0);
  });

  test("no refusal means no warning at all", async ({ page }) => {
    // The negative the ticket asks for. Without it, a gate that fired
    // unconditionally would pass every other test in this file.
    const rec = installMocks(page, { refuseTimes: 0 });
    await openOrganizePanel(page);

    await page.getByRole("checkbox", { name: /Sort my emails into folders/ }).check();
    await page.getByRole("button", { name: "Save" }).click();

    await expect(page.getByTestId("organize-save-msg")).toContainText(/Lumogis will organize/i);
    await expect(page.getByTestId("starvation-warning")).toHaveCount(0);
    expect(rec.patches()).toHaveLength(1);
    expect(rec.patches()[0]?.acknowledge_external_consumer).toBeUndefined();
  });

  test("a cleanup pass that moves mail warns on its own surface", async ({ page }) => {
    // The third arm, and a different component (EmailCleanupPass) reading the
    // same refusal through the same helper.
    installMocks(page, { cleanupRefuse: true });
    await page.goto("/settings/email");
    await expect(page.getByTestId("email-signals-view")).toBeVisible({ timeout: 15_000 });

    await page.getByText("Clean up this mailbox").click();
    await expect(page.getByRole("heading", { name: "Clean up my inbox" })).toBeVisible();
    await page.getByRole("button", { name: "Start cleanup" }).click();

    // Starting the pass writes nothing and cannot starve anything, so it is not
    // gated. Selecting a category to MOVE and approving is the armed action.
    await expect(page.getByTestId("cleanup-proposals")).toBeVisible({ timeout: 15_000 });
    await page.getByRole("checkbox", { name: /Promotional \/ bulk/ }).check();
    await page.getByRole("button", { name: /File selected into folders/ }).click();

    const warning = page.getByTestId("cleanup-starvation-warning");
    await expect(warning).toBeVisible();
    await expect(warning).toContainText(MESSAGE);
  });
});
