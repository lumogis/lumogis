// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-643: durable triage-state lane resolution for the Email surface.
//
// Replaces LUM-642's read-only `deriveLane` as the single lane seam. Real,
// user-settable state (needs_you / good_to_know / snoozed / done) wins; snooze
// re-surfaces at query time (the mailbox forgets; Lumogis remembers). When a
// signal has no triage row, we fall back to the provisional `deriveLane`
// derivation so the inbox is meaningful out of the box.

import type { EmailSignalRow } from "../../api/emailSources";
import { deriveLane, type Lane } from "./lanes";

/** A snoozed-but-not-yet-due signal sits outside the three lanes (in the
 *  Snoozed disclosure) until its `snoozed_until` passes. */
export type EffectiveLane = Lane | "snoozed_hidden";

/**
 * Resolve which lane a signal belongs in *now*.
 *
 *   done                              → "done"
 *   snoozed & snoozed_until > now     → "snoozed_hidden"  (Snoozed disclosure)
 *   snoozed & snoozed_until <= now    → "needs_you"       (re-surfaced, decision #2)
 *   needs_you / good_to_know          → that lane
 *   (no triage row / unknown status)  → deriveLane(signal) (default derivation)
 *
 * `now` is injectable for deterministic tests; defaults to the wall clock. Snooze
 * re-surface is purely a function of state + clock — no scheduler, client-clock
 * skew tolerated (re-derived on the next refetch/focus).
 */
export function effectiveLane(signal: EmailSignalRow, now: Date = new Date()): EffectiveLane {
  switch (signal.triage_status) {
    case "done":
      return "done";
    case "needs_you":
      return "needs_you";
    case "good_to_know":
      return "good_to_know";
    case "snoozed": {
      const until = signal.snoozed_until ? new Date(signal.snoozed_until) : null;
      if (until && !Number.isNaN(until.getTime()) && until.getTime() > now.getTime()) {
        return "snoozed_hidden";
      }
      // Expired (or malformed) snooze → re-surface to Needs you.
      return "needs_you";
    }
    default:
      // null / undefined / unknown status → provisional derivation.
      return deriveLane(signal);
  }
}

/** True iff the signal is snoozed and not yet due (belongs in the Snoozed
 *  disclosure, out of the three lanes). */
export function isSnoozedHidden(signal: EmailSignalRow, now: Date = new Date()): boolean {
  return effectiveLane(signal, now) === "snoozed_hidden";
}

/**
 * Collapse an owner's `scope='personal'` original and its `scope='shared'`
 * projection into a single row (LUM-661).
 *
 * When a member shares an email, the backend keeps the personal original AND
 * inserts a shared projection whose `published_from` points at the original's
 * `signal_id`; the household union returns *both* to the owner, so they'd see the
 * same email twice — each independently triageable. Rows are grouped by
 * `published_from ?? signal_id` (the original and its projection share that key).
 * The representative prefers a member the user has actually triaged (so triage on
 * either copy surfaces), then the personal original. Non-owners only ever see the
 * projection, so their single-member groups are untouched. Input order is
 * preserved by each group's first-seen member.
 */
export function collapseProjections(signals: EmailSignalRow[]): EmailSignalRow[] {
  const groups = new Map<string, EmailSignalRow[]>();
  for (const s of signals) {
    const key = s.published_from ?? s.signal_id;
    const g = groups.get(key);
    if (g) g.push(s);
    else groups.set(key, [s]);
  }
  const pick = (g: EmailSignalRow[]): EmailSignalRow => {
    if (g.length === 1) return g[0];
    const triaged = g.filter((s) => s.triage_status != null);
    if (triaged.length === 1) return triaged[0];
    // Both or neither triaged → prefer the personal original (no published_from).
    return g.find((s) => !s.published_from) ?? g[0];
  };
  const seen = new Set<string>();
  const out: EmailSignalRow[] = [];
  for (const s of signals) {
    const key = s.published_from ?? s.signal_id;
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(pick(groups.get(key) as EmailSignalRow[]));
  }
  return out;
}

/**
 * Snooze presets: label → a function returning the target wake time. Functions
 * (not fixed Dates) so each is computed relative to "now" at click time.
 */
export const SNOOZE_PRESETS: ReadonlyArray<{ label: string; at: (now?: Date) => Date }> = [
  {
    label: "Later today",
    at: (now = new Date()) => {
      const d = new Date(now);
      d.setHours(d.getHours() + 3, 0, 0, 0);
      return d;
    },
  },
  {
    label: "This evening",
    at: (now = new Date()) => {
      const d = new Date(now);
      d.setHours(18, 0, 0, 0);
      if (d.getTime() <= now.getTime()) d.setDate(d.getDate() + 1);
      return d;
    },
  },
  {
    label: "Tomorrow",
    at: (now = new Date()) => {
      const d = new Date(now);
      d.setDate(d.getDate() + 1);
      d.setHours(8, 0, 0, 0);
      return d;
    },
  },
  {
    label: "This weekend",
    at: (now = new Date()) => {
      const d = new Date(now);
      const daysToSat = (6 - d.getDay() + 7) % 7 || 7; // next Saturday
      d.setDate(d.getDate() + daysToSat);
      d.setHours(8, 0, 0, 0);
      return d;
    },
  },
  {
    label: "Next week",
    at: (now = new Date()) => {
      const d = new Date(now);
      const daysToMon = (8 - d.getDay()) % 7 || 7; // next Monday
      d.setDate(d.getDate() + daysToMon);
      d.setHours(8, 0, 0, 0);
      return d;
    },
  },
];
