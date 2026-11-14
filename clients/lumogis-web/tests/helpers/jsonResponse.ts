// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
/**
 * Shared test helper — JSON fetch Response (Vitest + jsdom).
 *
 * `headers` (LUM-849) is optional and merged after `Content-Type`, for the
 * routes whose answer is not fully in the body: `GET /me/permissions` reports an
 * incomplete list via a `Warning` advisory, and a test that cannot set one
 * cannot exercise that branch at all.
 */
export function jsonResponse(
  status: number,
  body: unknown,
  headers?: Record<string, string>,
): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", ...headers },
  });
}
