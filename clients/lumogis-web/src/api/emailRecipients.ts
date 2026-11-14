// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Read-only façade for GET /api/v1/email/recipients (LUM-818).
//
// `address` is the BARE addr-spec and is the only thing that may be inserted
// into a recipient field. `display_name` is for rendering. The backend's
// `_validate_recipient` rejects whitespace, so inserting a
// "Jane Smith <j@x.com>" form produces a 422 at proposal-create — long after
// the user typed it and with nothing on screen to explain why.

import type { ApiClient } from "./client";

export interface RecipientSuggestion {
  /** Bare addr-spec. Insert THIS, never the rendered form. */
  address: string;
  /** Present for correspondents we received mail from; empty for sent-only. */
  display_name: string;
  /** True when the user has written to this address, not merely received from it. */
  corresponded: boolean;
}

export interface RecipientSuggestionsResponse {
  suggestions: RecipientSuggestion[];
}

/** Below this the endpoint answers 400, so do not ask. Mirrors MIN_PREFIX_CHARS. */
export const MIN_QUERY_CHARS = 2;

export async function fetchRecipientSuggestions(
  api: ApiClient,
  query: string,
  signal?: AbortSignal,
): Promise<RecipientSuggestion[]> {
  const q = query.trim();
  if (q.length < MIN_QUERY_CHARS) return [];
  const res = await api.getJson<RecipientSuggestionsResponse>(
    `/api/v1/email/recipients?q=${encodeURIComponent(q)}`,
    { signal },
  );
  return res?.suggestions ?? [];
}
