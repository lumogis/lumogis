// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
// Email search over subject, sender and message text (LUM-819).
import type { ApiClient } from "./client";
import type { EmailSignalRow } from "./emailSources";

/** Which half of the search box answered. A hit can match several at once. */
export type MatchedIn = "subject" | "sender" | "summary" | "body";

/** Which fields to search. `all` is the default and what the box opens on. */
export type SearchFields = "all" | "subject" | "sender" | "body";

/**
 * A hit is a signal row plus three search-only fields — deliberately NOT a
 * narrower type. `collapseProjections` groups on `published_from` and
 * tie-breaks on `triage_status`, and `EmailSignalCard` reads several more; a
 * bespoke subset would break both silently.
 */
export interface EmailSearchHit extends EmailSignalRow {
  matched_in: MatchedIn[];
  /** Non-empty only when `matched_in` includes `body`. */
  snippet: string;
  /** The stored text was capped, so a miss inside this message may be an artefact. */
  body_truncated?: boolean;
}

/**
 * Per-field match counts. These are the point, not decoration: subject and
 * sender substring-match while message text word-matches, and the user cannot
 * see which engine answered. Search fails silently — an empty list looks
 * identical to an absent message — so `body: 0` beside `subject: 3` is what
 * explains the behaviour without any help text.
 */
export interface FieldCounts {
  all: number;
  subject: number;
  sender: number;
  body: number;
  /** A count hit the server's bound; render "200+" rather than the number. */
  capped: boolean;
}

export interface EmailSearchResponse {
  hits: EmailSearchHit[];
  counts: FieldCounts;
  fields: SearchFields;
  truncated: boolean;
  /** Oldest mail Lumogis HOLDS — not the mailbox's oldest message. */
  searched_from: string | null;
}

export const MIN_SEARCH_CHARS = 3;

export async function searchEmail(
  client: ApiClient,
  q: string,
  opts: { fields?: SearchFields; sourceId?: string; limit?: number } = {},
): Promise<EmailSearchResponse> {
  const params = new URLSearchParams({ q });
  if (opts.fields && opts.fields !== "all") params.set("fields", opts.fields);
  if (opts.sourceId) params.set("source_id", opts.sourceId);
  if (opts.limit) params.set("limit", String(opts.limit));
  return client.getJson(`/api/v1/email/search?${params.toString()}`);
}
