// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-728 — reading the one refusal on rule creation that the user is meant to
// answer rather than retry.
//
// Deliberately the same shape as `features/signals/externalConsumerWarning.ts`
// (LUM-729): both are consent gates on the same underlying fact — Lumogis and
// paperless want the same mailbox — so a user who has met one should recognise
// the other. Two differently-shaped mechanisms for one idea would teach them
// there is no pattern to learn.

import { ApiError } from "../../api/client";

export const OVERLAP_CODE = "paperless_may_also_file";

export interface PaperlessOverlapWarning {
  message: string;
  alternative: string;
  recovery: string;
  /** LUM-835 — present only on the `POST /sources` connect refusal, which names
   *  every rule-bearing mailbox paperless would also watch. Absent on the
   *  rule-creation refusal, which is single-mailbox by construction: the user is
   *  creating a rule ON a mailbox, so there is nothing to enumerate. Empty array
   *  rather than undefined for the common case, so a caller can map without a
   *  guard. */
  mailboxes: { source_name: string; rule_count: number }[];
}

/** The warning if this error is the overlap gate, else `null`.
 *
 *  Matches on the `code`, never on the copy. The server is free to reword
 *  `message` / `alternative` / `recovery` without a contract break, and a UI
 *  that branched on prose would silently stop recognising the refusal the first
 *  time someone edited it — which for a fail-open feature means it would simply
 *  stop warning, with nothing to show that it had. */
export function paperlessOverlapWarning(err: unknown): PaperlessOverlapWarning | null {
  if (!(err instanceof ApiError) || err.status !== 422) return null;
  let detail: unknown;
  try {
    detail = (JSON.parse(err.detail) as { detail?: unknown }).detail;
  } catch {
    return null;
  }
  if (typeof detail !== "object" || detail === null) return null;
  const d = detail as Record<string, unknown>;
  if (d.code !== OVERLAP_CODE) return null;
  return {
    message: typeof d.message === "string" ? d.message : "",
    alternative: typeof d.alternative === "string" ? d.alternative : "",
    recovery: typeof d.recovery === "string" ? d.recovery : "",
    mailboxes: Array.isArray(d.mailboxes)
      ? d.mailboxes.flatMap((m) => {
          if (typeof m !== "object" || m === null) return [];
          const row = m as Record<string, unknown>;
          const name = typeof row.source_name === "string" ? row.source_name : "";
          const count = typeof row.rule_count === "number" ? row.rule_count : 0;
          // A nameless mailbox cannot be pointed at, so it would render as a
          // count attached to nothing. Drop it rather than print "3 rules on ".
          return name ? [{ source_name: name, rule_count: count }] : [];
        })
      : [],
  };
}

/** Shown when the server sends the refusal without a `message`. The gate must
 *  never render blank: a warning nobody can see is a rule the user cannot
 *  create, with no explanation of why. */
export const OVERLAP_FALLBACK =
  "Paperless may already be watching this mailbox, so this rule could file the same document twice.";
