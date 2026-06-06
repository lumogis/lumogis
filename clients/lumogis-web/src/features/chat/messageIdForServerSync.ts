// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Map client-side chat message ids (e.g. ``u_<uuid>``) to UUIDs accepted by
// ``POST /api/v1/conversations/{id}/messages``.

const UUID_RE =
  /[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}/i;

/** Return a UUID string suitable for server transcript append (LUM-162). */
export function messageIdForServerSync(clientMessageId: string): string {
  const match = clientMessageId.match(UUID_RE);
  if (match) {
    return match[0].toLowerCase();
  }
  const c = (globalThis as { crypto?: { randomUUID?: () => string } }).crypto;
  if (c && typeof c.randomUUID === "function") {
    return c.randomUUID();
  }
  throw new Error("crypto.randomUUID is required for server message ids");
}
