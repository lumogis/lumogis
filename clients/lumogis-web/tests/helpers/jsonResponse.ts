// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
/** Shared test helper — JSON fetch Response (Vitest + jsdom). */
export function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}
