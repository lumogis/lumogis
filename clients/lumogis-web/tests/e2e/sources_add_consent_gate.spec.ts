// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-842 — e2e for the LUM-837 chunk B source-add consent gates, at the three
// paths the Linear issue names. Hybrid: smoke login + context.route mocks,
// same setup as email_starvation_gate.spec.ts (LUM-735).
//
// WHAT THE BROWSER CAN AND CANNOT PROVE HERE. Whether either gate SHOULD fire
// is a server predicate that deliberately fails open
// (`has_folder_sorting_mailbox`, `_paperless_overlap_matches`). No browser test
// can see that: with mocked routes the refusal is whatever the fixture says.
// Those predicates are covered in orchestrator/tests/test_external_consumer_guard.py
// and the paperless overlap suite.
//
// What a browser CAN prove, and what this file pins:
//
//   * Path 1 — two-arm sequence. The third confirm:true POST carries BOTH
//     acknowledgement flags. The server re-checks starvation unconditionally,
//     so a re-post that drops acknowledge_external_consumer loops forever.
//   * Path 2 — overlap only. The re-post carries acknowledge_paperless_overlap
//     and NOT acknowledge_external_consumer. A flag for a card never shown is
//     the failure mode.
//   * Path 3 — the credential seam. The link resolves to /me/connectors, not
//     /admin/connector-credentials (wrong tiers) and not bare /connectors
//     (redirects to /chat). Follow it, save where it actually goes, return,
//     the badge flips. A toHaveAttribute unit assertion cannot prove the
//     route resolves or that the round-trip refreshes presence.
//
// Intercept pathname === "/sources" only. Playwright glob **/sources also
// matches the SPA document GET /me/sources.
//
// Routes are registered on the browser CONTEXT so Path 3's target=_blank
// popup is mocked too (page.route would not follow).

import { test, expect, type Page, type Route } from "@playwright/test";

import { OVERLAP_CODE } from "../../src/features/email/paperlessOverlapWarning";
import { STARVATION_CODE } from "../../src/features/signals/externalConsumerWarning";
import {
  hasSmokeCreds,
  loginWithSmokeCredentials,
  smokeCredsSkipMessage,
} from "./smoke-auth";

const PAPERLESS_URL = "https://paperless.example.test";
const PAPERLESS_TOKEN = "tok-e2e-not-a-secret-12";

// Deliberately NOT the shipped copy. If the UI ever branched on prose instead
// of the code, these strings would stop matching and the dialog would vanish.
const STARVATION_MESSAGE = "FIXTURE: sorting into folders takes mail out of the inbox paperless watches.";
const STARVATION_ALTERNATIVE = "FIXTURE: use labels only.";
const STARVATION_RECOVERY = "FIXTURE: undo moves mail back.";
const OVERLAP_MESSAGE = "FIXTURE: paperless will also file attachments you already file.";
const OVERLAP_ALTERNATIVE = "FIXTURE: turn a rule off.";
const OVERLAP_RECOVERY = "FIXTURE: nothing is deleted.";

interface ConfirmBody {
  confirm?: boolean;
  source_type?: string;
  url?: string;
  acknowledge_external_consumer?: boolean;
  acknowledge_paperless_overlap?: boolean;
}

interface CredentialPut {
  path: string;
  body: { payload?: { base_url?: string; token?: string } };
}

interface Recorder {
  confirms: () => ConfirmBody[];
  credentialGets: () => number;
  credentialPuts: () => CredentialPut[];
}

function isSourcesApi(route: Route): boolean {
  if (route.request().resourceType() === "document") return false;
  try {
    return new URL(route.request().url()).pathname === "/sources";
  } catch {
    return false;
  }
}

function json(status: number, body: unknown): Parameters<Route["fulfill"]>[0] {
  return {
    status,
    contentType: "application/json",
    body: JSON.stringify(body),
  };
}

function starvationDetail() {
  return {
    detail: {
      code: STARVATION_CODE,
      message: STARVATION_MESSAGE,
      alternative: STARVATION_ALTERNATIVE,
      recovery: STARVATION_RECOVERY,
    },
  };
}

function overlapDetail() {
  return {
    detail: {
      code: OVERLAP_CODE,
      message: OVERLAP_MESSAGE,
      alternative: OVERLAP_ALTERNATIVE,
      recovery: OVERLAP_RECOVERY,
      mailboxes: [{ source_name: "Personal", rule_count: 3 }],
    },
  };
}

function paperlessRow() {
  return {
    connector: "paperless",
    instance: "",
    created_at: "2026-08-26T00:00:00Z",
    updated_at: "2026-08-26T00:00:00Z",
  };
}

/** `sequence` is consumed one per confirm:true POST: "starvation" | "overlap" | "created". */
async function installMocks(
  page: Page,
  opts: { sequence: Array<"starvation" | "overlap" | "created"> },
): Promise<Recorder> {
  const confirms: ConfirmBody[] = [];
  const credentialPuts: CredentialPut[] = [];
  const queue = [...opts.sequence];
  let credentialItems: unknown[] = [];
  let credentialGets = 0;

  const ctx = page.context();

  await ctx.route("**/api/v1/me/connector-credentials**", async (route) => {
    const method = route.request().method();
    const path = new URL(route.request().url()).pathname;
    if (method === "GET" && path === "/api/v1/me/connector-credentials/registry") {
      // Mocked, not continued: PaperlessForm is chosen by the client-side id
      // `paperless`, not by registry fields. A live registry that omitted the
      // card (or a locator that missed it among many Apply buttons) left Path 3
      // proving nothing. One paperless item makes Server address unique.
      await route.fulfill(
        json(200, {
          items: [
            {
              id: "paperless",
              description: "Paperless-ngx document store.",
              fields: [
                { name: "base_url", label: "Server address", is_secret: false },
                { name: "token", label: "API token", is_secret: true },
              ],
            },
          ],
        }),
      );
      return;
    }
    if (method === "GET" && path === "/api/v1/me/connector-credentials") {
      credentialGets += 1;
      await route.fulfill(json(200, { items: credentialItems }));
      return;
    }
    if (method === "PUT" && path === "/api/v1/me/connector-credentials/paperless") {
      const body = (route.request().postDataJSON() ?? {}) as CredentialPut["body"];
      credentialPuts.push({ path, body });
      const payload = body.payload ?? {};
      // Presence flips only when the production keys arrive. A fake that
      // accepted any body would hide a form that still writes `api_token`.
      if (typeof payload.base_url === "string" && typeof payload.token === "string") {
        credentialItems = [paperlessRow()];
      }
      await route.fulfill(json(200, {}));
      return;
    }
    await route.continue();
  });

  await ctx.route((url) => {
    try {
      return new URL(url).pathname === "/sources";
    } catch {
      return false;
    }
  }, async (route) => {
    if (!isSourcesApi(route)) return route.continue();
    const method = route.request().method();
    if (method === "GET") {
      await route.fulfill(json(200, { sources: [], total: 0 }));
      return;
    }
    if (method !== "POST") return route.continue();
    const body = (route.request().postDataJSON() ?? {}) as ConfirmBody;
    if (body.confirm === false) {
      await route.fulfill(
        json(200, {
          source_type: "paperless",
          url: body.url,
          preview_items: [],
        }),
      );
      return;
    }
    if (body.confirm !== true) {
      await route.fulfill(json(400, { detail: "confirm must be a boolean" }));
      return;
    }
    confirms.push(body);
    const next = queue.shift();
    if (next === undefined) {
      await route.fulfill(json(500, { detail: "unexpected extra confirm:true POST" }));
      return;
    }
    if (next === "starvation") {
      await route.fulfill(json(422, starvationDetail()));
      return;
    }
    if (next === "overlap") {
      await route.fulfill(json(422, overlapDetail()));
      return;
    }
    await route.fulfill(
      json(200, {
        status: "created",
        source_id: "src-e2e",
        source_type: "paperless",
        url: body.url,
        preview_items: [],
      }),
    );
  });

  return {
    confirms: () => confirms,
    credentialGets: () => credentialGets,
    credentialPuts: () => credentialPuts,
  };
}

async function reachAdd(page: Page) {
  await page.goto("/me/sources");
  await expect(page.getByRole("heading", { name: "Add a source" })).toBeVisible({
    timeout: 15_000,
  });
  await page.getByLabel("What are you adding?").selectOption("paperless");
  await page.getByLabel("Paperless address").fill(PAPERLESS_URL);
  await page.getByRole("button", { name: "Check" }).click();
  await expect(page.getByRole("button", { name: "Add" })).toBeVisible();
}

function expectConfirmTrue(body: ConfirmBody | undefined) {
  expect(body?.confirm).toBe(true);
}

async function expectSourceAdded(page: Page) {
  // Scoped into the add panel: SourcesView also publishes "Source added." as
  // its own labelled live region. Unscoped getByRole("status") is a
  // strict-mode violation (email_imap_sources lesson).
  const panel = page.getByRole("region", { name: "Add a source" });
  await expect(panel.getByRole("status", { name: "Source add result" })).toHaveText(
    "Source added.",
  );
}

/**
 * Return from the credentials popup the way a person does, then make React
 * Query notice.
 *
 * Playwright Pages in one context do not background the opener (measured
 * 2026-08-26: GET count stuck at 3 after popup.close + competing page).
 * TanStack Query v5's focusManager listens for `visibilitychange` on
 * `window` only — not `focus`. Chromium tab-return fires hidden → visible;
 * that pair is the harness equivalent. The GET-count assertion still dies
 * if `refetchOnWindowFocus` is removed.
 */
async function returnFocusTo(page: Page) {
  await page.bringToFront();
  await page.evaluate(() => {
    Object.defineProperty(document, "visibilityState", {
      configurable: true,
      get: () => "hidden",
    });
    Object.defineProperty(document, "hidden", {
      configurable: true,
      get: () => true,
    });
    window.dispatchEvent(new Event("visibilitychange"));
    Object.defineProperty(document, "visibilityState", {
      configurable: true,
      get: () => "visible",
    });
    Object.defineProperty(document, "hidden", {
      configurable: true,
      get: () => false,
    });
    window.dispatchEvent(new Event("visibilitychange"));
  });
}

test.describe("source add consent gates (LUM-842)", () => {
  test.skip(!hasSmokeCreds, smokeCredsSkipMessage);

  test.beforeEach(async ({ page }) => {
    await loginWithSmokeCredentials(page);
  });

  // LUM-282. One assertion, no new setup: `installMocks` already fulfils
  // `GET /sources` with an empty list, so the zero state is on screen the
  // moment `/me/sources` loads.
  test("the empty sources page offers document setup above the add form", async ({ page }) => {
    await installMocks(page, { sequence: ["created"] });
    await page.goto("/me/sources");

    const offer = page.getByRole("heading", {
      name: "No document archives, feeds or watched pages yet",
    });
    await expect(offer).toBeVisible({ timeout: 15_000 });
    await expect(page.getByText(/save its address and API token first/)).toBeVisible();

    // Above the add form, not below it — the placement is the point.
    const offerBox = await offer.boundingBox();
    const addBox = await page.getByRole("heading", { name: "Add a source" }).boundingBox();
    expect(offerBox && addBox && offerBox.y < addBox.y).toBe(true);
  });

  test("Path 1 — two-arm: the third request carries both acknowledgement flags", async ({
    page,
  }) => {
    const rec = await installMocks(page, { sequence: ["starvation", "overlap", "created"] });
    await reachAdd(page);

    await page.getByRole("button", { name: "Add" }).click();
    await expect(page.getByRole("heading", { name: "Paperless may miss some mail" })).toBeVisible();
    await expect(page.getByRole("alert")).toContainText(STARVATION_MESSAGE);

    expect(rec.confirms()).toHaveLength(1);
    expectConfirmTrue(rec.confirms()[0]);
    expect(rec.confirms()[0]?.acknowledge_external_consumer).toBeUndefined();
    expect(rec.confirms()[0]?.acknowledge_paperless_overlap).toBeUndefined();

    await page.getByRole("button", { name: "Connect anyway" }).click();
    await expect(page.getByRole("heading", { name: "Paperless may file these twice" })).toBeVisible();
    await expect(page.getByRole("alert")).toContainText("Personal");
    await expect(page.getByRole("alert")).toContainText("3 rules");

    await expect.poll(() => rec.confirms().length).toBe(2);
    expectConfirmTrue(rec.confirms()[1]);
    expect(rec.confirms()[1]?.acknowledge_external_consumer).toBe(true);
    expect(rec.confirms()[1]?.acknowledge_paperless_overlap).toBeUndefined();

    await page.getByRole("button", { name: "Connect anyway" }).click();
    await expect.poll(() => rec.confirms().length).toBe(3);
    expectConfirmTrue(rec.confirms()[2]);
    expect(rec.confirms()[2]).toMatchObject({
      confirm: true,
      acknowledge_external_consumer: true,
      acknowledge_paperless_overlap: true,
    });
    await expectSourceAdded(page);
  });

  test("Path 2 — overlap only: re-post carries overlap and not starvation", async ({
    page,
  }) => {
    const rec = await installMocks(page, { sequence: ["overlap", "created"] });
    await reachAdd(page);

    await page.getByRole("button", { name: "Add" }).click();
    await expect(page.getByRole("heading", { name: "Paperless may file these twice" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Paperless may miss some mail" })).toHaveCount(0);

    expectConfirmTrue(rec.confirms()[0]);
    expect(rec.confirms()[0]?.acknowledge_external_consumer).toBeUndefined();
    expect(rec.confirms()[0]?.acknowledge_paperless_overlap).toBeUndefined();

    await page.getByRole("button", { name: "Connect anyway" }).click();
    await expect.poll(() => rec.confirms().length).toBe(2);
    expectConfirmTrue(rec.confirms()[1]);
    expect(rec.confirms()[1]?.acknowledge_paperless_overlap).toBe(true);
    expect(rec.confirms()[1]?.acknowledge_external_consumer).toBeUndefined();
    await expectSourceAdded(page);
  });

  test("Path 3 — credential link resolves to /me/connectors and the badge flips", async ({
    page,
  }) => {
    const rec = await installMocks(page, { sequence: ["created"] });
    await page.goto("/me/sources");
    await expect(page.getByRole("heading", { name: "Add a source" })).toBeVisible({
      timeout: 15_000,
    });
    await page.getByLabel("What are you adding?").selectOption("paperless");

    const addLink = page.getByRole("link", { name: "Add them" });
    await expect(addLink).toHaveAttribute("href", "/me/connectors");
    await expect(addLink).toHaveAttribute("target", "_blank");
    await expect(page.getByText("No paperless credentials yet.")).toBeVisible();

    const popupPromise = page.waitForEvent("popup");
    await addLink.click();
    const popup = await popupPromise;
    await popup.waitForLoadState("domcontentloaded");
    await expect(popup).toHaveURL(/\/me\/connectors\/?$/);
    await expect(popup).not.toHaveURL(/\/admin\/connector-credentials/);
    await expect(popup).not.toHaveURL(/\/chat/);
    await expect(popup.getByLabel("Email")).toHaveCount(0);

    await expect(popup.getByRole("heading", { name: "Connectors" })).toBeVisible({
      timeout: 15_000,
    });
    // The card title is the registry id (`paperless`), not the description
    // string ("Paperless-ngx…"). A locator for the word "Paperless" does not
    // match. The chip sits in the same <h3>, so the accessible name is
    // `paperless` + chip with no word boundary — `/^paperless\b/` misses it.
    await expect(popup.getByRole("heading", { name: /^paperless/i })).toBeVisible({
      timeout: 15_000,
    });

    await popup.getByLabel("Server address").fill(PAPERLESS_URL);
    await popup.getByLabel("API token").fill(PAPERLESS_TOKEN);
    await popup.getByRole("button", { name: "Apply" }).click();
    await expect(popup.getByRole("status").filter({ hasText: /^Saved\.?$/ })).toBeVisible();

    expect(rec.credentialPuts()).toHaveLength(1);
    expect(rec.credentialPuts()[0]?.path).toBe("/api/v1/me/connector-credentials/paperless");
    expect(rec.credentialPuts()[0]?.body).toMatchObject({
      payload: { base_url: PAPERLESS_URL, token: PAPERLESS_TOKEN },
    });

    const getsAfterSave = rec.credentialGets();
    await popup.close();
    await returnFocusTo(page);

    await expect.poll(() => rec.credentialGets()).toBeGreaterThan(getsAfterSave);
    await expect(page.getByText("Paperless credentials saved.")).toBeVisible({
      timeout: 15_000,
    });
    await expect(page.getByText("No paperless credentials yet.")).toHaveCount(0);
  });
});
