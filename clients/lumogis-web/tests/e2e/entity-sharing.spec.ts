// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-581 — single-user household entity share lifecycle on /search
// (publish → "Shared" badge → "Shared with household" filter → unshare reverts).
// Requires live stack + smoke credentials (same contract as documents.spec.ts).
//
// Hybrid pattern (same as documents.spec.ts): real smoke login + stateful
// page.route mocks for the KG search / entity read / entity publish routes, so
// the full owner UI flow is asserted deterministically without a live Qdrant
// projection or a second household member. (Two-user cross-visibility and the
// derived share_status/is_owner contract are proven in the pytest integration
// test tests/integration/test_household_sharing.py S15–S17.)

import { test, expect, type Page, type Route } from "@playwright/test";

import {
  hasSmokeCreds,
  loginWithSmokeCredentials,
  smokeCredsSkipMessage,
} from "./smoke-auth";

const SHARED_ENTITY_ID = "ent-shared-0581";
const OTHER_ENTITY_ID = "ent-other-0581";
const SHARED_ENTITY_NAME = "Acme Corporation";
const OTHER_ENTITY_NAME = "Private Contact";
const QUERY = "corp";

type EntityShareStatus = "personal" | "shared";

interface ShareMockState {
  sharedStatus: EntityShareStatus;
}

function entityCard(
  entityId: string,
  name: string,
  shareStatus: EntityShareStatus,
): Record<string, unknown> {
  return {
    entity_id: entityId,
    name,
    type: "organization",
    aliases: [],
    summary: null,
    sources: [],
    // Owner keeps the personal source row (owner-projection collapse); only
    // share_status flips — mirrors the backend derivation for the owner.
    scope: "personal",
    owner_user_id: null,
    share_status: shareStatus,
    is_owner: true,
  };
}

async function mockEntityShareRoutes(page: Page, state: ShareMockState): Promise<void> {
  // Auto-accept the plain-language share confirm (window.confirm in the toggle).
  page.on("dialog", (dialog) => void dialog.accept());

  // Memory column — keep it empty so the entity column is the only surface.
  await page.route("**/api/v1/memory/search**", async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ hits: [], degraded: false, reason: null }),
    });
  });

  // KG search — the target entity (current lifecycle state) + a control that
  // is never shared, so the "Shared with household" filter is meaningful.
  await page.route("**/api/v1/kg/search**", async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        entities: [
          entityCard(SHARED_ENTITY_ID, SHARED_ENTITY_NAME, state.sharedStatus),
          entityCard(OTHER_ENTITY_ID, OTHER_ENTITY_NAME, "personal"),
        ],
      }),
    });
  });

  // Related — the card panel fetches this alongside the entity; keep it empty.
  await page.route(
    `**/api/v1/kg/entities/${SHARED_ENTITY_ID}/related**`,
    async (route: Route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ related: [] }),
      });
    },
  );

  // Publish / unpublish (synchronous 200/204) — flip the mock lifecycle state
  // so the subsequent card + search refetch reflects the new share status.
  await page.route(
    `**/api/v1/entities/${SHARED_ENTITY_ID}/publish`,
    async (route: Route) => {
      const method = route.request().method();
      if (method === "POST") {
        state.sharedStatus = "shared";
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ resource: "entities", scope: "shared" }),
        });
        return;
      }
      if (method === "DELETE") {
        state.sharedStatus = "personal";
        await route.fulfill({ status: 204, body: "" });
        return;
      }
      await route.continue();
    },
  );

  // Entity read (exact id, no trailing segment) — state-dependent card. Declared
  // after /related so the more specific related glob keeps its own handler.
  await page.route(`**/api/v1/kg/entities/${SHARED_ENTITY_ID}`, async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(entityCard(SHARED_ENTITY_ID, SHARED_ENTITY_NAME, state.sharedStatus)),
    });
  });
}

test.describe("LUM-581 household entity sharing (/search)", () => {
  test.skip(!hasSmokeCreds, smokeCredsSkipMessage);

  test("owner shares an entity: confirm → badge → filter → unshare reverts", async ({
    page,
  }) => {
    const state: ShareMockState = { sharedStatus: "personal" };
    await mockEntityShareRoutes(page, state);
    await loginWithSmokeCredentials(page);

    const entityButton = () =>
      page.locator("button.lumogis-search__entity-btn").filter({ hasText: SHARED_ENTITY_NAME });
    const filter = () => page.getByRole("checkbox", { name: "Shared with household" });
    const sharedBadge = () => entityButton().locator(".lumogis-search__shared-badge");

    // Search: entity column lists the target as "Personal" (no shared badge).
    await page.goto("/search");
    await page.getByLabel("Search query").fill(QUERY);
    await expect(entityButton()).toBeVisible({ timeout: 15_000 });
    await expect(sharedBadge()).toHaveCount(0);

    // Filter before sharing: nothing shared yet → empty-shared message.
    await filter().check();
    await expect(page.getByText("No shared entities.")).toBeVisible();
    await filter().uncheck();

    // Open the card: owner sees the interactive toggle, unchecked + "Personal".
    await entityButton().click();
    const toggle = page.getByTestId("entity-share-toggle");
    const shareSwitch = toggle.getByRole("switch");
    await expect(shareSwitch).not.toBeChecked();
    await expect(toggle).toContainText("Personal");

    // Share: confirm dialog auto-accepted → flips to "Shared" + household hint.
    await shareSwitch.click();
    await expect(shareSwitch).toBeChecked({ timeout: 15_000 });
    await expect(toggle).toContainText("Shared");
    await expect(page.getByText("Everyone in your household can find this.")).toBeVisible();

    // List badge now reads "Shared"; the filter keeps only the shared entity.
    await expect(sharedBadge()).toBeVisible({ timeout: 15_000 });
    await filter().check();
    await expect(entityButton()).toBeVisible();
    await expect(page.getByRole("button", { name: new RegExp(OTHER_ENTITY_NAME) })).toHaveCount(0);
    await filter().uncheck();

    // Unshare: switch is checked (no confirm on unshare) → reverts to "Personal".
    if ((await entityButton().getAttribute("aria-expanded")) !== "true") {
      await entityButton().click();
    }
    const toggleAfter = page.getByTestId("entity-share-toggle");
    const switchAfter = toggleAfter.getByRole("switch");
    await expect(switchAfter).toBeChecked({ timeout: 15_000 });
    await switchAfter.click();
    await expect(switchAfter).not.toBeChecked({ timeout: 15_000 });
    await expect(toggleAfter).toContainText("Personal");
    await expect(page.getByText("Everyone in your household can find this.")).toHaveCount(0);

    // List reverts: the shared badge is gone again.
    await expect(sharedBadge()).toHaveCount(0, { timeout: 15_000 });
  });
});
