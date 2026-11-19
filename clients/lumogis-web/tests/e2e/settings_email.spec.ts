// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Email accounts and Email labels on the shared components (LUM-993 plan B
// Pass 4), against a running stack with smoke credentials. Labels, rules and
// mailboxes are answered from an in-memory fake, so the smoke account is not
// changed and the test does not depend on what it has connected. What the
// server does with a label (sorting, the sweep to Other) is tested in the
// orchestrator suite; this spec checks the page flow, axe and phone layout.

import { expect, test, type Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

import { hasSmokeCreds, loginWithSmokeCredentials, smokeCredsSkipMessage } from "./smoke-auth";

type Cat = { slug: string; label: string; enabled: boolean; is_custom: boolean; is_protected: boolean; sort: number };

function fakeEmailApi(page: Page) {
  const state = {
    categories: [
      { slug: "school", label: "School", enabled: true, is_custom: false, is_protected: false, sort: 0 },
      { slug: "other", label: "Other", enabled: true, is_custom: false, is_protected: true, sort: 9 },
    ] as Cat[],
    rules: [] as Array<Record<string, unknown>>,
    taxPuts: [] as unknown[],
    removed: [] as string[],
  };
  const json = (body: unknown) => ({ status: 200, contentType: "application/json", body: JSON.stringify(body) });

  void page.route("**/api/v1/me/email-taxonomy", async (route) => {
    if (route.request().method() === "PUT") {
      const patch = route.request().postDataJSON() as { categories?: Partial<Cat>[]; deletes?: string[] };
      state.taxPuts.push(patch);
      for (const c of patch.categories ?? []) {
        const found = state.categories.find((x) => x.slug === c.slug);
        if (found) Object.assign(found, c);
        else state.categories.push({ enabled: true, is_custom: true, is_protected: false, sort: 5, ...c } as Cat);
      }
      state.categories = state.categories.filter((x) => !(patch.deletes ?? []).includes(x.slug));
    }
    await route.fulfill(json({ categories: state.categories }));
  });
  void page.route("**/api/v1/me/email-classification-rules", async (route) => {
    if (route.request().method() === "PUT") state.rules = (route.request().postDataJSON() as { rules: [] }).rules;
    await route.fulfill(json({ rules: state.rules }));
  });
  // Mailboxes: data requests only (a page load of /settings/sources must still get the app).
  void page.route("**/sources", async (route) => {
    if (route.request().resourceType() === "document") return route.fallback();
    await route.fulfill(
      json({
        total: 1,
        sources: [
          {
            id: "src-family",
            name: "Family Gmail",
            source_type: "imap",
            url: "imaps://imap.gmail.com:993/INBOX",
            category: "email",
            active: true,
            poll_interval: 300,
            extraction_method: "imap_metadata",
            full_ingest_active: false,
            last_polled_at: null,
            last_signal_at: null,
            polling_active: true,
            last_poll_error_code: null,
            attachment_ingest_enabled: false,
          },
        ],
      }),
    );
  });
  void page.route("**/sources/src-family", async (route) => {
    if (route.request().method() === "DELETE") state.removed.push("src-family");
    await route.fulfill(json({ status: "ok", source_id: "src-family" }));
  });
  return state;
}

async function axeClean(page: Page): Promise<void> {
  const axe = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa"]).analyze();
  const serious = axe.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
  expect(serious, JSON.stringify(serious, null, 2)).toHaveLength(0);
}

async function noSidewaysScroll(page: Page): Promise<void> {
  const over = await page.evaluate(() => {
    const main = document.getElementById("lumogis-main");
    return Math.max(document.body.scrollWidth - document.body.clientWidth, main ? main.scrollWidth - main.clientWidth : 0);
  });
  expect(over).toBeLessThanOrEqual(1);
}

for (const [name, viewport] of [
  ["wide screen", { width: 1280, height: 900 }],
  ["phone", { width: 390, height: 844 }],
] as const) {
  test.describe(`Email settings, ${name}`, () => {
    test.skip(!hasSmokeCreds, smokeCredsSkipMessage);
    test.use({ viewport, hasTouch: name === "phone" });

    test("labels: add from a name, rename, turn off after asking; rules: add and delete", async ({ page }) => {
      const api = fakeEmailApi(page);
      await loginWithSmokeCredentials(page);
      await page.goto("/settings/email-labels");
      await expect(page.getByRole("heading", { level: 1, name: "Email labels and rules" })).toBeVisible({ timeout: 30_000 });

      await page.getByLabel("Label name").fill("Receipts");
      await page.getByRole("button", { name: "Add label" }).click();
      await expect(page.getByRole("button", { name: "Actions for Receipts" })).toBeVisible();
      expect(api.taxPuts).toContainEqual({ categories: [{ slug: "receipts", label: "Receipts" }] });

      await page.getByRole("button", { name: "Actions for School" }).click();
      await page.getByRole("menuitem", { name: "Rename" }).click();
      await page.getByLabel("Rename School").fill("Kids' school");
      await page.getByRole("button", { name: "Save" }).click();
      await expect(page.getByRole("button", { name: "Actions for Kids' school" })).toBeVisible();

      await page.getByRole("button", { name: "Actions for Kids' school" }).click();
      await page.getByRole("menuitem", { name: "Turn off" }).click();
      const dialog = page.getByRole("alertdialog", { name: "Turn off Kids' school?" });
      await expect(dialog).toContainText("moves to Other");
      await dialog.getByRole("button", { name: "Turn off" }).click();
      await expect(page.getByText("Off", { exact: true })).toBeVisible();

      await page.getByLabel("Subject contains").fill("invoice, receipt");
      await page.getByRole("combobox", { name: "Rule label" }).click();
      await page.getByRole("option", { name: "Receipts" }).click();
      await page.getByRole("button", { name: "Add rule" }).click();
      const sentence = "subject contains invoice, receipt → Receipts";
      await expect(page.getByText(sentence)).toBeVisible();
      await page.getByRole("button", { name: `Actions for rule: ${sentence}` }).click();
      await page.getByRole("menuitem", { name: "Delete rule" }).click();
      await page.getByRole("alertdialog", { name: "Delete this rule?" }).getByRole("button", { name: "Delete" }).click();
      await expect(page.getByText("No rules yet. Add one below.")).toBeVisible();

      await noSidewaysScroll(page);
      await axeClean(page);
    });

    test("accounts: a mailbox card, the Email page link, and Remove asks first", async ({ page }) => {
      const api = fakeEmailApi(page);
      await loginWithSmokeCredentials(page);
      await page.goto("/settings/email");
      await expect(page.getByRole("heading", { name: "Family Gmail" })).toBeVisible({ timeout: 30_000 });
      await expect(page.getByTestId("mode-src-family")).toContainText("Subject and sender only");
      await expect(page.getByRole("link", { name: /Email page/ })).toHaveAttribute("href", "/email");

      await page.getByRole("button", { name: "More actions for Family Gmail" }).click();
      await page.getByRole("menuitem", { name: "Remove mailbox" }).click();
      const dialog = page.getByRole("alertdialog");
      await expect(dialog).toContainText("Family Gmail");
      await dialog.getByRole("button", { name: "Cancel" }).click();
      await expect(dialog).toHaveCount(0);
      expect(api.removed).toEqual([]);

      await noSidewaysScroll(page);
      await axeClean(page);
    });
  });
}
