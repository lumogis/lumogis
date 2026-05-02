// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Phase 4D — pure helpers for Web Push payload + notification click targets.
// Used from `sw.ts` (no network, no Cache Storage, no secrets in logs).
//

/** Default copy when payload is missing or unusable (must stay generic). */
export const PUSH_FALLBACK_TITLE = "Lumogis";
export const PUSH_FALLBACK_BODY = "Lumogis has an update";
export const PUSH_FALLBACK_TAG = "lumogis-update";

const MAX_TITLE_CHARS = 120;
const MAX_BODY_CHARS = 240;
const MAX_TAG_CHARS = 64;

/** Same-origin in-app routes only (exact path; no query/hash). */
export const NOTIFICATION_ALLOWED_PATHS = [
  "/",
  "/chat",
  "/approvals",
  "/me/notifications",
] as const;

const ALLOWED = new Set<string>(NOTIFICATION_ALLOWED_PATHS);

function truncate(s: string, max: number): string {
  if (s.length <= max) return s;
  return s.slice(0, max);
}

/**
 * Strip query/hash, reject absolute or scheme URLs, allow only exact allowlisted paths.
 * Returns a path starting with "/" safe for `new URL(path, origin)`.
 */
export function sanitizeNotificationClickUrl(raw: string | undefined | null): string {
  if (raw == null || typeof raw !== "string") return "/";

  const trimmed = raw.trim();
  if (trimmed === "") return "/";

  const lower = trimmed.toLowerCase();
  if (lower.startsWith("javascript:") || lower.startsWith("data:")) return "/";

  if (trimmed.includes("://")) return "/";
  if (trimmed.startsWith("//")) return "/";

  if (!trimmed.startsWith("/")) return "/";

  const pathOnly = trimmed.split(/[?#]/u, 1)[0] ?? "/";
  const noTrail =
    pathOnly.length > 1 ? pathOnly.replace(/\/+$/u, "") || "/" : pathOnly;

  if (!ALLOWED.has(noTrail)) return "/";
  return noTrail;
}

export type NormalizedPushPayload = {
  title: string;
  body: string;
  tag: string;
  /** Sanitized path for `notification.data.url` and showNotification options. */
  targetPath: string;
};

/**
 * Parse decrypted push JSON (unknown shape). Unknown fields ignored.
 * Uses generic defaults when parsing fails or values are malformed.
 */
export function normalizePushPayloadFromJson(parsed: unknown): NormalizedPushPayload {
  const defaults: NormalizedPushPayload = {
    title: PUSH_FALLBACK_TITLE,
    body: PUSH_FALLBACK_BODY,
    tag: PUSH_FALLBACK_TAG,
    targetPath: "/",
  };

  if (parsed === null || typeof parsed !== "object") {
    return defaults;
  }

  const o = parsed as Record<string, unknown>;

  let title = PUSH_FALLBACK_TITLE;
  if (typeof o.title === "string" && o.title.trim() !== "") {
    title = truncate(o.title.trim(), MAX_TITLE_CHARS);
  }

  let body = PUSH_FALLBACK_BODY;
  if (typeof o.body === "string" && o.body.trim() !== "") {
    body = truncate(o.body.trim(), MAX_BODY_CHARS);
  }

  let tag = PUSH_FALLBACK_TAG;
  if (typeof o.tag === "string" && o.tag.trim() !== "") {
    tag = truncate(o.tag.trim(), MAX_TAG_CHARS);
  }

  const targetPath =
    typeof o.url === "string" ? sanitizeNotificationClickUrl(o.url) : "/";

  return {
    title,
    body,
    tag,
    targetPath,
  };
}
