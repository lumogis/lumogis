// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-414 — Playwright chat e2e for the /chat conversation history UI
// (sidebar list, delete, and slice-2 server transcript restore). Parent:
// LUM-162 (conversation history UI,
// docs/decisions/074-lum-162-conversation-history-ui.md).
//
// Harness contract (same as first_slice / admin_shell / onboarding specs):
//   * The Lumogis stack (Caddy + lumogis-web + orchestrator + Postgres + the
//     summarization batch worker) must be running so the SPA shell, the real
//     auth flow, and POST /session/end -> sessions row pipeline all work.
//     There is no Playwright `webServer` in playwright.config.ts, so these
//     tests test.skip without smoke creds + a live stack.
//   * Credentials (family-LAN smoke user):
//       export LUMOGIS_WEB_SMOKE_EMAIL=...
//       export LUMOGIS_WEB_SMOKE_PASSWORD='...'   # >= 12 chars
//   * Optional: PLAYWRIGHT_BASE_URL=http://other-host
//
// Backend coupling — IMPORTANT:
//   * Auth (/api/v1/auth/*), /api/v1/models, POST /session/end, GET
//     /api/v1/conversations, and DELETE /api/v1/conversations/{id} all hit the
//     REAL backend. The List/Delete rows are produced by the real session/end
//     harness (`createEndedSession`), not synthetic page.route() data.
//   * Only two transport routes remain mocked, because they are not the focus
//     of this gap and keep the reload assertion deterministic:
//       - POST /api/v1/conversations/{id}/continue  (KEEP — deterministic seed)
//       - POST /api/v1/conversations/{id}/messages  (KEEP — debounced sync)
//     plus PUT /api/v1/conversations/{id} (KEEP — best-effort upsert on mount).
//     DELETE on the same /{id} path is explicitly passed through to the real
//     backend (route.continue()).
//
// Coverage note (partial-delete UX):
//   The `partial:true` retry-toast branch (ADR-074) is a best-effort purge
//   FAULT path that a healthy backend will not return on demand; it is
//   therefore not exercised here now that DELETE is real network. It remains a
//   unit-level concern (ConversationSidebar). See LUM-414 report.

import { test, expect, type Page, type Route } from "@playwright/test";

import { createEndedSession } from "./session-harness";
import {
  hasSmokeCreds,
  loginWithSmokeCredentials,
  smokeCredsSkipMessage,
} from "./smoke-auth";

function historyRow(sidebar: ReturnType<Page["getByTestId"]>, conversationId: string) {
  return sidebar.locator(`[data-conversation-id="${conversationId}"]`);
}

interface KeptRouteOptions {
  /** Verbatim slice-2 messages returned by the (mocked) POST .../continue. */
  continueSeedMessages?: Array<{ role: "user" | "assistant" | "system"; content: string }>;
}

/**
 * Register only the routes we still mock: continue (deterministic seed),
 * messages (debounced sync), and the best-effort PUT upsert. GET list, DELETE,
 * and POST /session/end are intentionally NOT registered here — they hit the
 * real backend (the three former PLACEHOLDER stubs were removed for LUM-414
 * gap #1).
 */
async function mockKeptConversationRoutes(
  page: Page,
  opts: KeptRouteOptions = {},
): Promise<void> {
  const continueSeedMessages = opts.continueSeedMessages ?? [];

  // KEEP: PUT /{id} upsert; DELETE /{id} passes through to the real backend.
  // Registered first so the more specific continue/messages routes below win.
  await page.route("**/api/v1/conversations/*", async (route: Route) => {
    if (route.request().method() === "PUT") {
      const id = new URL(route.request().url()).pathname.split("/").pop() ?? "";
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          conversation_id: id,
          title: "New chat",
          summary: "",
          ended_at: new Date().toISOString(),
          scope: "personal",
          message_count: 0,
        }),
      });
      return;
    }
    // DELETE (and anything else on /{id}) -> real network.
    await route.continue();
  });

  // KEEP: POST /{id}/continue -> deterministic verbatim seed for the reload test.
  await page.route("**/api/v1/conversations/*/continue", async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ seed_messages: continueSeedMessages, conversation_id: null }),
    });
  });

  // KEEP: POST /{id}/messages -> debounced slice-2 sync ack.
  await page.route("**/api/v1/conversations/*/messages", async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        message_id: "00000000-0000-4000-8000-000000000099",
        role: "assistant",
        content: "",
        created_at: new Date().toISOString(),
        model: null,
      }),
    });
  });
}

test.describe("LUM-414 chat conversation history (desktop)", () => {
  // Same contract as the rest of the e2e suite: requires a live stack + creds.
  test.skip(!hasSmokeCreds, smokeCredsSkipMessage);
  test.describe.configure({ timeout: 180_000 });

  test("List: a real ended session renders in the History sidebar under Today", async ({
    page,
  }) => {
    await mockKeptConversationRoutes(page);
    await loginWithSmokeCredentials(page);
    await expect(page).toHaveURL(/\/chat$/);

    // Real fixture: enqueue session/end and poll the real conversations list.
    const created = await createEndedSession(page, {
      messages: [
        { role: "user", content: "Remind me what the roofer quoted." },
        { role: "assistant", content: "2,400 for the garage roof last spring." },
      ],
    });

    // Re-fetch the sidebar now that the row exists server-side.
    await page.reload();
    const sidebar = page.getByTestId("conversation-sidebar");
    await expect(sidebar).toBeVisible();
    await expect(historyRow(sidebar, created.conversation_id)).toBeVisible();
    // Real ended_at is recent -> client groups it under "Today".
    await expect(sidebar.getByText("Today", { exact: true })).toBeVisible();
  });

  test("Delete: removing a real row drops it from the sidebar (real DELETE)", async ({
    page,
  }) => {
    await mockKeptConversationRoutes(page);

    // onDelete() calls window.confirm(); Playwright auto-dismisses dialogs
    // (-> false) unless we accept them.
    page.on("dialog", (dialog) => void dialog.accept());

    await loginWithSmokeCredentials(page);
    const created = await createEndedSession(page);
    await page.reload();

    const sidebar = page.getByTestId("conversation-sidebar");
    const row = historyRow(sidebar, created.conversation_id);
    await expect(row).toBeVisible();

    // Real DELETE /api/v1/conversations/{id} (route.continue passthrough).
    await row.locator(".lumogis-chat__history-delete").click({ force: true });

    await expect(historyRow(sidebar, created.conversation_id)).toHaveCount(0);
  });

  test("Reload (slice-2): server transcript is restored into the chat via continue-from-history", async ({
    page,
  }) => {
    // The shipped slice-2 server -> transcript path. continue is mocked here so
    // the restored transcript is deterministic regardless of how the backend
    // summarized the real session created below.
    const restoredUser = "What did the roofer quote last time?";
    const restoredAssistant = "They quoted 2,400 for the garage roof.";

    await mockKeptConversationRoutes(page, {
      continueSeedMessages: [
        { role: "user", content: restoredUser },
        { role: "assistant", content: restoredAssistant },
      ],
    });

    await loginWithSmokeCredentials(page);
    const created = await createEndedSession(page);
    await page.reload();

    const sidebar = page.getByTestId("conversation-sidebar");
    const row = historyRow(sidebar, created.conversation_id);
    await expect(row).toBeVisible();

    // Selecting a history row triggers continueConversation() -> the verbatim
    // slice-2 transcript loads into the active thread.
    await row.locator(".lumogis-chat__history-select").click({ force: true });

    const transcript = page.getByRole("log", { name: /conversation transcript/i });
    await expect(transcript.getByText(restoredUser)).toBeVisible();
    await expect(transcript.getByText(restoredAssistant)).toBeVisible();
  });

  // /chat?session=<id> URL hydration is implemented in ChatPage (reads the
  // `session` param on mount and dispatches LOAD_SEED_MESSAGES via
  // continueConversation). Kept as a fixme here to preserve the stable 4-entry
  // shape; can be promoted to an active test in a dedicated follow-up.
  test(
    "Reload (slice-2): /chat?session=<id> restores transcript from server",
    async ({ page }) => {
      // continue is mocked (same helper as test 3) so the restored transcript
      // is deterministic regardless of how the backend summarized the session.
      const restoredUser = "What did the roofer quote last time?";
      const restoredAssistant = "They quoted 2,400 for the garage roof.";

      await mockKeptConversationRoutes(page, {
        continueSeedMessages: [
          { role: "user", content: restoredUser },
          { role: "assistant", content: restoredAssistant },
        ],
      });

      await loginWithSmokeCredentials(page);
      const created = await createEndedSession(page);

      // Navigate to the deep link. ChatPage reads ?session=<id> on mount ->
      // continueConversation(client, id) -> dispatch LOAD_SEED_MESSAGES (the
      // mocked continue seed above), restoring the transcript without a click.
      await page.goto(`/chat?session=${created.conversation_id}`);

      const transcript = page.getByRole("log", { name: /conversation transcript/i });
      await expect(transcript.getByText(restoredUser)).toBeVisible();
      await expect(transcript.getByText(restoredAssistant)).toBeVisible();
    },
  );
});
