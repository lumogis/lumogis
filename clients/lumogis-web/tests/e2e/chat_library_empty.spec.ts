// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-853 — chat's empty-library state, rendered in a real browser.
//
// `GET /api/v1/documents` is fixed with `page.route` (pathname predicate). The condition under
// test is client logic ("thread empty AND the list loaded with zero rows"), not what the server
// lists, and a fresh shared smoke stack has documents from other specs.
//
// NOT covered here, on purpose:
//   - the boot states (first-admin setup, still starting, sign-in off): Lumogis Web no longer has
//     an auth-off mode (LUM-981). They are covered in Vitest by tests/auth/setupBoot.test.tsx
//     (W0-W7) and ChatPage test 14.
//   - the server's list contents: pytest (orchestrator/tests/test_api_v1_documents.py).
//   - whether chat really mounts the documents query and gates its stream: Vitest ChatPage
//     tests 5 and 9.
//
// The overflow measure is the `document.body` form on purpose (see forgot_password.spec.ts):
// eight other specs measure `documentElement`, which reads 0 even when content overflows
// (the app clips with overflow-x: hidden), so they cannot fail. Follow-up, in-code (LUM-853).

import { test, expect, type Page } from "@playwright/test";

import { hasSmokeCreds, loginWithSmokeCredentials, smokeCredsSkipMessage } from "./smoke-auth";

const EMPTY_TITLE = "No documents in your library yet";

const ONE_DOC = {
  document_id: 1,
  in_flight_job_id: null,
  display_name: "notes.md",
  file_path: "/library/notes.md",
  file_type: ".md",
  chunk_count: 3,
  entity_count: 0,
  scope: "personal",
  status: "indexed",
  indexed_at: "2026-11-01T00:00:00Z",
  error_message: null,
};

async function routeDocuments(page: Page, documents: unknown[]): Promise<void> {
  await page.route(
    (url) => url.pathname === "/api/v1/documents",
    (route) =>
      route.request().method() === "GET"
        ? route.fulfill({ json: { documents } })
        : route.continue(),
  );
}

/**
 * An empty library is a first-run user, who has no chat history either. The shared smoke user
 * accumulates History from other specs, and on a phone the conversation list stacks above the
 * chat, so without this the message box is pushed below the fold by unrelated state.
 */
async function routeNoConversations(page: Page): Promise<void> {
  await page.route(
    (url) => url.pathname === "/api/v1/conversations",
    (route) =>
      route.request().method() === "GET"
        ? route.fulfill({ json: { conversations: [] } })
        : route.continue(),
  );
}

/** Pixels by which page content exceeds the viewport width, or <= 0 if it fits. */
async function horizontalOverflowPx(page: Page): Promise<number> {
  return page.evaluate(() => document.body.scrollWidth - window.innerWidth);
}

test.describe("LUM-853 chat empty-library state", () => {
  test.skip(!hasSmokeCreds, smokeCredsSkipMessage);

  test("desktop: empty library offers sources and the link lands on a usable page", async ({ page }) => {
    await routeDocuments(page, []);
    await loginWithSmokeCredentials(page);
    await page.goto("/chat");

    const heading = page.getByRole("heading", { name: EMPTY_TITLE });
    await expect(heading).toBeVisible({ timeout: 30_000 });
    await page.getByRole("link", { name: "Connect a source" }).click();
    await expect(page).toHaveURL(/\/settings\/sources$/);
    await expect(page.getByRole("heading", { level: 1, name: "Documents, feeds and pages" })).toBeVisible();
  });

  test("mobile 390x844: the empty-library state is usable", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await routeDocuments(page, []);
    await routeNoConversations(page);
    await loginWithSmokeCredentials(page);
    await page.goto("/chat");

    const heading = page.getByRole("heading", { name: EMPTY_TITLE });
    await expect(heading).toBeVisible({ timeout: 30_000 });

    for (const name of ["Connect a source", "Upload a file"]) {
      const box = await page.getByRole("link", { name }).boundingBox();
      expect(box, `${name} has a box`).not.toBeNull();
      expect(box!.x).toBeGreaterThanOrEqual(0);
      expect(box!.x + box!.width).toBeLessThanOrEqual(390);
    }
    expect(await horizontalOverflowPx(page)).toBeLessThanOrEqual(0);
    await expect(page.getByRole("textbox", { name: "Message" })).toBeInViewport();

    // The EmptyState sits below the transcript, not inside it.
    const log = await page.getByRole("log", { name: /conversation transcript/i }).boundingBox();
    const h = await heading.boundingBox();
    expect(log && h && h.y >= log.y + log.height - 1).toBeTruthy();

    await page.getByRole("link", { name: "Connect a source" }).click();
    await expect(page).toHaveURL(/\/settings\/sources$/);
  });

  test("with a document, chat is unchanged", async ({ page }) => {
    await routeDocuments(page, [ONE_DOC]);
    await loginWithSmokeCredentials(page);
    await page.goto("/chat");

    await expect(page.getByRole("combobox", { name: "Model" })).toBeVisible({ timeout: 30_000 });
    // LUM-993: the one history line replaced the old "ephemeral" sentences.
    await expect(page.getByText("Open chats stay in this tab. Closed chats are kept as summaries in History.")).toBeVisible();
    await expect(page.getByRole("heading", { name: EMPTY_TITLE })).toHaveCount(0);
  });
});
