// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Playwright session/end harness for the conversation-history e2e (LUM-414).
// Drives the REAL backend: enqueues a session end via POST /session/end and
// polls the real GET /api/v1/conversations list until summarization writes the
// `sessions` row (the queued session_end job is asynchronous — the row is not
// available synchronously after the 200).
//
// Request/response shapes confirmed from the backend integration test
// `orchestrator/tests/test_api_v1_conversations.py::test_session_end_to_list_e2e`
// and `orchestrator/routes/data.py::session_end`:
//   POST /session/end  { session_id: <uuid>, messages: [{ role, content }] }
//                      -> 200 { status: "session end queued", session_id }
//   GET  /api/v1/conversations
//                      -> { conversations: [{ conversation_id, title, summary,
//                                             ended_at, scope, message_count }] }

import { expect, type Page } from "@playwright/test";

export interface EndedSessionMessage {
  role: "user" | "assistant" | "system";
  content: string;
}

export interface EndedSessionInput {
  /** UUID v4; becomes conversation_id === session_id (ADR-074). Auto-minted if omitted. */
  sessionId?: string;
  messages?: EndedSessionMessage[];
  /** Poll budget (defaults: 10 attempts × 500 ms). */
  maxAttempts?: number;
  intervalMs?: number;
}

export interface EndedSessionSummary {
  conversation_id: string;
  title: string;
  summary: string;
  ended_at: string;
}

function uuidV4(): string {
  return globalThis.crypto.randomUUID();
}

/**
 * Mint a short-lived access token from the existing refresh cookie WITHOUT a
 * second login. A fresh login as the same smoke user would revoke the
 * browser's refresh jti (single-active-jti family-LAN model — see
 * playwright.config.ts), so we reuse the page context's shared cookie jar via
 * `page.request` and call POST /api/v1/auth/refresh instead.
 */
async function mintAccessToken(page: Page): Promise<string> {
  const origin = process.env.PLAYWRIGHT_BASE_URL ?? "http://127.0.0.1";
  // Run refresh in the page (not page.request): the HttpOnly refresh cookie path is
  // /api/v1/auth and is reliably attached to same-origin fetch with credentials.
  const body = await page.evaluate(async (originHeader) => {
    const res = await fetch("/api/v1/auth/refresh", {
      method: "POST",
      credentials: "include",
      headers: { Origin: originHeader },
    });
    if (!res.ok) {
      throw new Error(`session-harness: /api/v1/auth/refresh failed (HTTP ${res.status})`);
    }
    return (await res.json()) as { access_token?: string };
  }, origin);
  if (typeof body.access_token !== "string" || body.access_token.length === 0) {
    throw new Error("session-harness: refresh response missing access_token");
  }
  return body.access_token;
}

/**
 * POST /session/end for a real session, then poll GET /api/v1/conversations
 * until the summarized row appears. Returns the real conversation summary
 * (id, title, summary, ended_at) so specs assert against real data, not
 * synthetic fixtures.
 *
 * Uses the authenticated browser context (no extra login). Requires a running
 * stack with the summarization batch worker; callers gate with
 * test.skip(!hasSmokeCreds).
 */
export async function createEndedSession(
  page: Page,
  input: EndedSessionInput = {},
): Promise<EndedSessionSummary> {
  const sessionId = input.sessionId ?? uuidV4();
  const messages = input.messages ?? [
    { role: "user", content: "What did the roofer quote for the garage roof?" },
    { role: "assistant", content: "They quoted 2,400 for the garage roof last spring." },
  ];
  // Batch queue ticks every ~5s; summarization calls Ollama — allow up to ~90s on RC stacks.
  const maxAttempts = input.maxAttempts ?? 90;
  const intervalMs = input.intervalMs ?? 1000;

  const token = await mintAccessToken(page);
  const headers = { Authorization: `Bearer ${token}` };

  const end = await page.request.post("/session/end", {
    headers: { ...headers, "Content-Type": "application/json" },
    data: { session_id: sessionId, messages },
  });
  expect(
    end.ok(),
    `session-harness: POST /session/end failed (HTTP ${end.status()})`,
  ).toBeTruthy();

  for (let attempt = 0; attempt < maxAttempts; attempt++) {
    const res = await page.request.get("/api/v1/conversations", { headers });
    if (res.ok()) {
      const body = (await res.json()) as { conversations?: EndedSessionSummary[] };
      const found = body.conversations?.find((c) => c.conversation_id === sessionId);
      if (found) {
        return {
          conversation_id: found.conversation_id,
          title: found.title,
          summary: found.summary,
          ended_at: found.ended_at,
        };
      }
    }
    await page.waitForTimeout(intervalMs);
  }

  throw new Error(
    `session-harness: session ${sessionId} did not appear in /api/v1/conversations ` +
      `after ${maxAttempts} attempts (${maxAttempts * intervalMs} ms). ` +
      "Is the summarization batch worker running on the stack under test?",
  );
}
