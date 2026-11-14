// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-729 — reading the one refusal the user is meant to act on.
//
// Every other error from these routes is a diagnostic: show it, the user tries
// again. This one is a *question* — "you also run paperless; sorting into
// folders takes mail out of the inbox it watches" — and the answer changes what
// the next request contains. So it needs to be recognised, not just displayed.
//
// The server sends `detail` as an object; the shared client's `safeReadDetail`
// can only hand back a string, so it JSON-stringifies the whole body. Parsing it
// back is the existing convention here (see `AdminSharedItemsView`,
// `AdminAuditView`) rather than something invented for this feature.

import { ApiError } from "../../api/client";

export const STARVATION_CODE = "external_consumer_may_starve";

export interface StarvationWarning {
  message: string;
  alternative: string;
  recovery: string;
}

/** The warning if this error is the starvation gate, else `null`.
 *
 *  Matches on the `code`, never on the copy — the copy is allowed to change
 *  without a contract break, and a UI that branched on prose would silently stop
 *  recognising the refusal the first time someone reworded it. */
export function starvationWarning(err: unknown): StarvationWarning | null {
  if (!(err instanceof ApiError) || err.status !== 422) return null;
  let detail: unknown;
  try {
    detail = (JSON.parse(err.detail) as { detail?: unknown }).detail;
  } catch {
    return null;
  }
  if (typeof detail !== "object" || detail === null) return null;
  const d = detail as Record<string, unknown>;
  if (d.code !== STARVATION_CODE) return null;
  return {
    message: typeof d.message === "string" ? d.message : "",
    alternative: typeof d.alternative === "string" ? d.alternative : "",
    recovery: typeof d.recovery === "string" ? d.recovery : "",
  };
}
