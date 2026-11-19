// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-989 — Admin → Knowledge graph on a Release install: Turn on, the low-memory
// confirmation, Turn off, and the offer line in System status, on desktop and on a
// phone-sized screen.
//
// Hybrid: smoke login on a live stack + `page.route` mocks for
// /api/v1/admin/knowledge-graph. The mocks walk the page through its states; what
// is asserted is the page's behaviour (which controls appear, the confirmation
// gate, which request it sends and with what body, the dismissible line), not the
// states themselves.
//
// Run (see `make web-e2e-prove`):
//   export LUMOGIS_WEB_SMOKE_EMAIL=...  LUMOGIS_WEB_SMOKE_PASSWORD='...'  # >= 12 chars
//   export LUMOGIS_E2E_EXPECT_ADMIN=1
//   npx playwright test tests/e2e/admin_knowledge_graph_toggle.spec.ts --project=chromium-smoke-shared-user
//
// NOT covered here, on purpose (vacuous-test principle: do not assert
// server-computed logic through the browser):
//   - which state the server derives, when each button is available, the memory
//     verdict, and the 409 refusals: pytest (test_graph_status.py,
//     test_graph_toggle.py, test_api_v1_admin_knowledge_graph_toggle.py).
//   - the polling cadence and its survival of failed requests: Vitest with fake
//     timers (AdminKnowledgeGraphToggle.test.tsx).
//   - a real Compose start and stop, the 1 GB pull, backup and restore: the
//     LUM-989 Pass 6 live proof on a release-shaped install.

import { test, expect, type Page } from "@playwright/test";

import { hasSmokeCreds, loginWithSmokeCredentials, smokeCredsSkipMessage } from "./smoke-auth";

const expectAdmin =
  process.env.LUMOGIS_E2E_EXPECT_ADMIN === "1"
    ? ""
    : "Set LUMOGIS_E2E_EXPECT_ADMIN=1 (admin smoke user required).";

const GIB = 1024 ** 3;
const DISMISS_KEY = "lumogis:graph-offer-dismissed";

function status(state: string, over: Record<string, unknown> = {}) {
  const on = state === "on";
  const transient = state === "starting" || state === "stopping";
  return {
    state,
    mode: on ? "service" : "disabled",
    configured_mode: on ? "service" : "disabled",
    service: {
      registered: on,
      healthy: on ? true : null,
      last_seen_healthy: null,
      last_unhealthy_reason: null,
    },
    memory: { total_bytes: 16 * GIB, required_bytes: 10 * GIB, enough: true },
    install_kind: "release",
    turn_on: transient
      ? { available: false, block: "in_progress", needs_confirmation: false }
      : on
        ? { available: false, block: "already_on", needs_confirmation: false }
        : { available: true, block: null, needs_confirmation: false },
    turn_off: transient
      ? { available: false, block: "in_progress" }
      : on
        ? { available: true, block: null }
        : { available: false, block: "already_off" },
    failed_code: null,
    ...over,
  };
}

/** off -> (enable) starting -> on -> (disable) stopping -> off; each GET after a write advances once. */
async function installMocks(
  page: Page,
  initial: Record<string, unknown>,
): Promise<{ posts: { path: string; body: unknown }[] }> {
  const posts: { path: string; body: unknown }[] = [];
  let current: Record<string, unknown> = initial;
  let after: Record<string, unknown> | null = null;
  await page.route("**/api/v1/admin/knowledge-graph**", async (route) => {
    const req = route.request();
    const path = new URL(req.url()).pathname;
    if (req.method() === "GET") {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(current) });
      if (after) {
        current = after;
        after = null;
      }
      return;
    }
    posts.push({ path, body: req.postDataJSON() });
    if (path.endsWith("/enable")) {
      current = status("starting");
      after = status("on");
      await route.fulfill({ status: 202, contentType: "application/json", body: '{"state":"starting"}' });
      return;
    }
    if (path.endsWith("/disable")) {
      current = status("stopping");
      after = status("off");
      await route.fulfill({ status: 202, contentType: "application/json", body: '{"state":"stopping"}' });
      return;
    }
    await route.fulfill({ status: 409, contentType: "application/json", body: '{"detail":"busy"}' });
  });
  return { posts };
}

async function onThenOff(page: Page): Promise<void> {
  const { posts } = await installMocks(page, status("off"));
  await page.goto("/settings/knowledge-graph");
  await expect(page.getByRole("heading", { level: 1, name: "Knowledge graph" })).toBeVisible();
  await expect(page.getByRole("status").filter({ hasText: "The knowledge graph is off." })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Add the knowledge graph" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Turn off" })).toHaveCount(0);

  await page.getByRole("button", { name: "Turn on" }).click();
  await expect(page.getByRole("status").filter({ hasText: "Starting the knowledge graph…" })).toBeVisible();
  await expect(page.getByRole("button", { name: /Turn o/ })).toHaveCount(0);
  await expect(page.getByRole("status").filter({ hasText: "The knowledge graph is on." })).toBeVisible({
    timeout: 20_000,
  });
  expect(posts[0]).toEqual({
    path: "/api/v1/admin/knowledge-graph/enable",
    body: { confirm_low_memory: false },
  });

  await page.getByRole("button", { name: "Turn off" }).click();
  await page.getByRole("button", { name: "Yes, turn off" }).click();
  await expect(page.getByRole("status").filter({ hasText: "Stopping the knowledge graph…" })).toBeVisible();
  await expect(page.getByRole("status").filter({ hasText: "The knowledge graph is off." })).toBeVisible({
    timeout: 20_000,
  });
  expect(posts.map((p) => p.path)).toEqual([
    "/api/v1/admin/knowledge-graph/enable",
    "/api/v1/admin/knowledge-graph/disable",
  ]);
}

async function lowMemory(page: Page): Promise<void> {
  const { posts } = await installMocks(
    page,
    status("off_low_memory", {
      memory: { total_bytes: 8 * GIB, required_bytes: 10 * GIB, enough: false },
      turn_on: { available: true, block: null, needs_confirmation: true },
    }),
  );
  await page.goto("/settings/knowledge-graph");
  await page.getByRole("button", { name: "Turn on" }).click();
  const confirm = page.getByRole("button", { name: "Turn on anyway" });
  await expect(confirm).toBeDisabled();
  expect(posts).toHaveLength(0);
  await page.getByRole("checkbox", { name: "I understand, turn it on anyway" }).check();
  await confirm.click();
  await expect(page.getByRole("status").filter({ hasText: "Starting the knowledge graph…" })).toBeVisible();
  expect(posts[0].body).toEqual({ confirm_low_memory: true });
}

async function offerLine(page: Page): Promise<void> {
  await installMocks(page, status("off"));
  await page.goto("/settings/health");
  const line = page.getByTestId("graph-offer-line");
  await expect(line).toBeVisible();
  await line.getByRole("link", { name: "Open Knowledge graph" }).click();
  await expect(page).toHaveURL(/\/settings\/knowledge-graph$/);
  await page.goto("/settings/health");
  await page.getByTestId("graph-offer-line").getByRole("button", { name: "Dismiss" }).click();
  await expect(page.getByTestId("graph-offer-line")).toHaveCount(0);
  await page.reload();
  await expect(page.getByRole("heading", { level: 1, name: "Health and backups" })).toBeVisible();
  await expect(page.getByTestId("graph-offer-line")).toHaveCount(0);
  await page.evaluate((k) => localStorage.removeItem(k), DISMISS_KEY);
}

test.describe("LUM-989 admin knowledge graph toggle", () => {
  test.skip(!hasSmokeCreds, smokeCredsSkipMessage);
  test.skip(!!expectAdmin, expectAdmin);

  test("desktop: turn on, then turn off", async ({ page }) => {
    await loginWithSmokeCredentials(page);
    await onThenOff(page);
  });

  test("desktop: low memory needs the I understand box", async ({ page }) => {
    await loginWithSmokeCredentials(page);
    await lowMemory(page);
  });

  test("desktop: System status offers it once and remembers the dismissal", async ({ page }) => {
    await loginWithSmokeCredentials(page);
    await offerLine(page);
  });

  test.describe("mobile (390x844)", () => {
    test.use({ viewport: { width: 390, height: 844 } });

    test("turn on, then turn off, with no sideways scroll", async ({ page }) => {
      await loginWithSmokeCredentials(page);
      await onThenOff(page);
      const overflow = await page.evaluate(
        () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
      );
      expect(overflow).toBeLessThanOrEqual(1);
    });

    test("low memory confirmation", async ({ page }) => {
      await loginWithSmokeCredentials(page);
      await lowMemory(page);
    });
  });
});
