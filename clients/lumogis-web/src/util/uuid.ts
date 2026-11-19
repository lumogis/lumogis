// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// A random UUID (version 4) that also works on plain-HTTP pages.
//
// Browsers expose crypto.randomUUID only in a secure context (HTTPS or
// localhost). A household opening Lumogis as http://<machine>:<port> from
// another device is not one, and Chat crashed there ("crypto.randomUUID is
// required for chat thread ids") while Capture sent a non-UUID client id the
// server rejects. crypto.getRandomValues has no such restriction, so it is the
// fallback; both are cryptographically random.

/** Return a lowercase RFC 4122 version 4 UUID. */
export function randomUuid(): string {
  const c = globalThis.crypto as Crypto | undefined;
  if (c && typeof c.randomUUID === "function") {
    try {
      return c.randomUUID();
    } catch {
      /* fall through: some browsers throw instead of leaving it undefined */
    }
  }
  if (!c || typeof c.getRandomValues !== "function") {
    throw new Error("This browser has no secure random number source");
  }
  const b = c.getRandomValues(new Uint8Array(16));
  b[6] = (b[6]! & 0x0f) | 0x40; // version 4
  b[8] = (b[8]! & 0x3f) | 0x80; // RFC 4122 variant
  const h = Array.from(b, (x) => x.toString(16).padStart(2, "0")).join("");
  return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`;
}
