// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-642: provisional lane derivation for the Email surface.
//
// This is the SINGLE seam LUM-643 rewrites when durable, user-settable triage
// state (needs-you / good-to-know / snoozed / done) lands. In v1 the lane is
// derived read-only from data already on the wire:
//   - `notified` requires a curated RelevanceProfile AND relevance >= threshold
//     AND a successful dispatch — so most households have it false everywhere.
//   - `importance_score` is ALWAYS computed by the classifier, so it co-drives
//     Needs-you to keep the lane meaningful out of the box.
// `done` is never produced in v1 (no per-signal state yet); the Done lane
// renders an empty placeholder until LUM-643.

import type { EmailSignalRow } from "../../api/emailSources";

export type Lane = "needs_you" | "good_to_know" | "done";

/** v1 constant (not user-tunable here); LUM-643 revisits the weighting. */
export const NEEDS_YOU_IMPORTANCE = 0.6;

export function deriveLane(signal: EmailSignalRow): Lane {
  // LUM-649: the per-caller feedback-learned nudge is added to importance_score
  // before the threshold — an additive shift of the margin, never a replacement
  // of the AI's baseline judgment (`?? 0` so an older backend is a no-op).
  const importance = (signal.importance_score ?? 0) + (signal.learned_relevance_delta ?? 0);
  if (signal.notified === true || importance >= NEEDS_YOU_IMPORTANCE) {
    return "needs_you";
  }
  return "good_to_know";
}
