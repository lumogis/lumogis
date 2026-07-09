// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-502 — Playwright smoke for document-scoped chat (/documents/:documentId/chat, LUM-175).
// Real smoke login + page.route mocks (same hybrid pattern as chat-conversation-history.spec.ts):
// the document-detail fetch and the chat-completions SSE stream are mocked so the test
// deterministically asserts the assistant response renders and the Context-used strip shows
// when citations are returned — without depending on a seeded document, Qdrant chunks, or a
// live LLM (the seeded round-trip is LUM-503).

import { test, expect, type Route } from "@playwright/test";

import { hasSmokeCreds, loginWithSmokeCredentials, smokeCredsSkipMessage } from "./smoke-auth";

const DOCUMENT_ID = 123;
const MOCK_TITLE = "E2E Mock Document";
const MOCK_FILE_PATH = "/library/e2e-mock.pdf";
const ASSISTANT_REPLY = "The document says hello.";

/** SSE body mirroring routes/chat.py chat-completion chunks (see src/api/chat.ts). */
function buildChatSse(): string {
  const base = { id: "e2e-doc-chat", object: "chat.completion.chunk", created: 0, model: "mock" };
  const events = [
    // First chunk: assistant role + the document citations (drives ContextUsedStrip).
    {
      ...base,
      choices: [{ index: 0, delta: { role: "assistant", content: "" }, finish_reason: null }],
      lumogis: {
        context_citations: [
          { chunk_index: 2, file_path: MOCK_FILE_PATH, score: 0.92, score_kind: "rerank" },
        ],
      },
    },
    { ...base, choices: [{ index: 0, delta: { content: "The document " }, finish_reason: null }] },
    { ...base, choices: [{ index: 0, delta: { content: "says hello." }, finish_reason: null }] },
    { ...base, choices: [{ index: 0, delta: {}, finish_reason: "stop" }] },
  ];
  const lines = events.map((e) => `data: ${JSON.stringify(e)}\n\n`);
  lines.push("data: [DONE]\n\n");
  return lines.join("");
}

async function mockDocumentChatRoutes(page: import("@playwright/test").Page): Promise<void> {
  // Deterministic single-model catalog (page hides the selector when models.length <= 1).
  await page.route("**/api/v1/models", async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ models: [{ id: "mock", label: "Mock", enabled: true }] }),
    });
  });

  // Document detail for the <h1> title — exact id path (glob * is one segment only).
  await page.route(`**/api/v1/documents/${DOCUMENT_ID}`, async (route: Route) => {
    if (route.request().method() !== "GET") {
      await route.continue();
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        document_id: DOCUMENT_ID,
        title: MOCK_TITLE,
        file_path: MOCK_FILE_PATH,
        scope: "personal",
        status: "indexed",
        source_available: true,
        entities: [],
      }),
    });
  });

  // Chat completions: canned SSE stream with citations + deltas + [DONE].
  await page.route("**/api/v1/chat/completions", async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: "text/event-stream",
      body: buildChatSse(),
    });
  });
}

test.describe("LUM-502 document chat (/documents/:documentId/chat)", () => {
  test.skip(!hasSmokeCreds, smokeCredsSkipMessage);

  test("send a message: assistant response renders with the Context used strip", async ({
    page,
  }) => {
    await mockDocumentChatRoutes(page);
    await loginWithSmokeCredentials(page);

    await page.goto(`/documents/${DOCUMENT_ID}/chat`);

    // Title (mocked detail) + empty state before any message.
    await expect(page.getByRole("heading", { name: MOCK_TITLE })).toBeVisible({ timeout: 15_000 });
    await expect(page.getByText("Ask about this document")).toBeVisible();

    await page.getByPlaceholder("Ask about this document…").fill("What does this say?");
    await page.getByRole("button", { name: "Send" }).click();

    // Assistant reply streamed from the mocked SSE.
    await expect(page.getByText(ASSISTANT_REPLY)).toBeVisible({ timeout: 15_000 });

    // Context used strip is shown because the stream returned citations.
    const strip = page.getByTestId("context-used-strip");
    await expect(strip).toBeVisible();
    await expect(strip).toContainText("Context used: chunks 2");
  });
});
