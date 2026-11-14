// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-849 — connector id → the words a person reads on the autonomy page.
//
// Split from the view so the fallback arm is unit-testable without rendering.
// That arm is the one least likely to be exercised by hand and most likely to
// be wrong, because in the default deployment no connector ever reaches it.
// Convention follows `features/signals/categoryLabels.ts`.

export interface PermGroup {
  /** The connector id this group speaks for. */
  key: string;
  /** The question, in the words a person would use. Never a connector id. */
  title: string;
  /** What is actually covered — the concrete acts, not a category. */
  detail: string;
  /** Why it matters. Consequence, not reassurance. */
  consequence: string;
  askLabel: string;
  doLabel: string;
  /**
   * The scope line. Load-bearing, and identical across groups on purpose.
   *
   * `connector_permissions` is `UNIQUE (user_id, connector)` — there is no
   * instance column and there is not going to be one (ADR: autonomy is
   * per-connector, capability is per-mailbox). Without this line a person
   * reasonably assumes a per-mailbox setting, because every OTHER email control
   * they have met in this product is per-mailbox.
   */
  scopeNote: string;
  /** True when this group's actions cannot be undone by anyone. */
  irreversible: boolean;
}

const SCOPE_NOTE = "Applies to all your mailboxes.";

/**
 * Keyed by connector id. **Iteration order is rendering order**, and
 * irreversible comes first deliberately: the question whose consequences a
 * person cannot take back is the one they should read while still paying
 * attention, not the one below the fold.
 *
 * The split is by CONNECTOR, verified against the live action registry
 * (2026-08-25): every `smtp` action is irreversible and received by someone
 * else; every `imap` action is reversible and private to your own mailbox.
 * It is deliberately NOT derived from each action's `is_reversible` flag —
 * that would surface two internal reverse actions (`email_save_draft_reverse`,
 * and `imap_writeback`'s reversal) which have `input_model=None` and cannot be
 * invoked by anything.
 */
export const PERMISSION_GROUPS: Record<string, PermGroup> = {
  smtp: {
    key: "smtp",
    title: "Send email as you",
    detail: "Replies, forwards, new messages, and unsubscribes.",
    consequence: "Other people receive these, and they cannot be taken back.",
    askLabel: "Ask me first",
    doLabel: "Just send",
    scopeNote: SCOPE_NOTE,
    irreversible: true,
  },
  imap: {
    key: "imap",
    title: "Tidy your mailbox",
    detail: "Save drafts, flag, label, mark as read, and move messages.",
    consequence: "Only you see these, and they can be undone.",
    askLabel: "Ask me first",
    doLabel: "Just do it",
    scopeNote: SCOPE_NOTE,
    irreversible: false,
  },
};

/**
 * The group for a connector, or an honest fallback for one we have no words for.
 *
 * The fallback is not a nicety. A connector reaching this page is a connector
 * that can ACT ON YOUR BEHALF — that is what having an Ask/Do mode means. If an
 * unrecognised one were dropped from the render, the page would omit a live
 * permission while claiming to list what Lumogis may do, which is a worse
 * failure than showing an ugly id. So: show the id, say plainly that we do not
 * have a description for it, and let the person decide anyway.
 *
 * This arm goes live the first time a capability service is configured
 * (`CAPABILITY_SERVICE_URLS` is unset by default) — see LUM-851.
 */
export function groupFor(connector: string): PermGroup {
  const known = PERMISSION_GROUPS[connector];
  if (known) return known;
  return {
    key: connector,
    title: connector,
    detail: "Lumogis can act on your behalf here.",
    consequence: "We don't have a plain-language description for this one yet.",
    askLabel: "Ask me first",
    doLabel: "Just do it",
    scopeNote: SCOPE_NOTE,
    irreversible: false,
  };
}

/**
 * Every connector to render, in order: the ones we have words for, then any the
 * server sent that we do not.
 *
 * The UNION is the point, and each half fixes a different failure:
 *
 *  - Known-first means a group whose row is MISSING still renders its question.
 *    Under the registry-degrade branch the server returns explicit rows only, so
 *    a person who never changed their `imap` setting has no `imap` row — and
 *    iterating the response alone would silently drop the whole "Tidy your
 *    mailbox" question off a page whose entire job is listing what may act on
 *    your behalf.
 *  - Appending unknowns means a row we have no copy for still renders. Iterating
 *    the known map alone would make `groupFor`'s fallback dead code.
 *
 * Neither input alone is correct, which is why this returns the union and why
 * two separate mutation checks cover it.
 */
export function connectorsToRender(rowConnectors: readonly string[]): string[] {
  const known = Object.keys(PERMISSION_GROUPS);
  const extras = rowConnectors.filter((c) => !(c in PERMISSION_GROUPS));
  return [...known, ...Array.from(new Set(extras))];
}
