// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-849 — the `/api/v1/me/permissions` wire contract, including its degrade
// signal.
//
// Why this module exists when every other surface's calls were inline here
// until now: the list route can tell you its answer is INCOMPLETE, and it says
// so in a `Warning` header. `ApiClient.getJson` returns the parsed body and
// throws the `Response` away, so no caller using it can ever see that. Reading
// the header means holding the `Response`, which means `client.fetch`, which
// means re-stating the two things `getJson` was doing for free — see both
// comments below. Neither is optional.

import { ApiError, apiErrorFrom, type ApiClient } from "./client";

/** One connector's effective Ask/Do state. Mirrors `ConnectorPermissionPublic`. */
export interface PermRow {
  connector: string;
  mode: "ASK" | "DO";
  /**
   * Granted permission scopes (LUM-612).
   *
   * Carried, NOT rendered in this chunk. The wire has sent this since LUM-612
   * and typing it honestly costs nothing, but giving it a display treatment
   * without a capability connector to test against would be inventing a
   * contract. LUM-851 renders it alongside the rows that need it.
   */
  scopes: string[];
  /** True when no explicit row exists and this reflects the lazy ASK default. */
  is_default: boolean;
  updated_at: string | null;
}

export interface PermListResult {
  rows: PermRow[];
  /**
   * False when the server said this list is missing rows it could not resolve.
   *
   * NOT the same as "the request failed" — a `complete: false` result is a
   * successful response carrying real rows plus an admission that there may be
   * more. The page must render the rows AND say so.
   */
  complete: boolean;
}

/**
 * The advisory the route sends when the action registry is unreachable.
 *
 * Matched on the warn-CODE, not on the header's presence. `Warning` is a
 * general-purpose HTTP header: a cache or proxy may legitimately emit
 * `110 Response is stale`, and treating that as "your permission list is
 * incomplete" would put a false alarm on a complete list — the same
 * says-the-wrong-thing defect this feature exists to remove, pointed the other
 * way.
 */
const REGISTRY_UNAVAILABLE_WARN_CODE = "199";

function isIncomplete(res: Response): boolean {
  const warning = res.headers.get("Warning");
  if (!warning) return false;
  // RFC 7234 warn-code is the first token: `199 - "text"`.
  return warning.trim().split(/\s+/)[0] === REGISTRY_UNAVAILABLE_WARN_CODE;
}

/**
 * GET the caller's permission rows, and whether the list is complete.
 *
 * Uses `client.fetch` (not `getJson`) to keep the `Response` for its header.
 * `client.fetch` does the 401-refresh-retry that `getJson` relies on, but it
 * does NOT check `res.ok` — so the throw below is not defensive padding, it is
 * the guarantee `getJson` was providing. Without it a 500 resolves as a
 * successful query whose `rows` is whatever `res.json()` made of an error body,
 * `isError` never fires, and the page renders an empty permission list: the
 * worst possible lie for this surface.
 */
export async function fetchMyPermissions(client: ApiClient): Promise<PermListResult> {
  const res = await client.fetch("/api/v1/me/permissions", { method: "GET" });
  if (!res.ok) throw await apiErrorFrom(res);
  const rows = (await res.json()) as PermRow[];
  return { rows, complete: !isIncomplete(res) };
}

/** PUT one connector's mode. Returns the new effective row (the route sends it). */
export async function putMyPermission(
  client: ApiClient,
  connector: string,
  mode: "ASK" | "DO",
): Promise<PermRow> {
  const res = await client.fetch(`/api/v1/me/permissions/${encodeURIComponent(connector)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ mode }),
  });
  if (!res.ok) throw await apiErrorFrom(res);
  return (await res.json()) as PermRow;
}

export { ApiError };
