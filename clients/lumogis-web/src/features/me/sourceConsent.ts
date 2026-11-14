// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-837 chunk B — the consent sequence for `POST /sources`, as a reducer.
//
// This is a state machine and it is written as one deliberately. Three rounds of
// plan review described it in prose and each description was correct on one path
// and wrong on another: a six-row event table traced the two-arm sequence and
// silently omitted the overlap-only user, while the test wording said each
// re-post carries its own flag "only" — right for the single-arm paths and an
// infinite loop for the two-arm one.
//
// One rule replaces all of it:
//
//   **Every request body carries exactly the flags that are `true` in `acked`
//   at the moment it is sent — no more, no fewer.**
//
// Every path falls out of that. The first Add sends neither because `acked` is
// `{false, false}`. Pressing a card's "Connect anyway" sets that card's arm and
// re-posts. A later request carries an earlier acknowledgement because it is
// still `true`, not because a table said "both".
//
// Two rules carry over from `features/signals/EmailOrganizeSettings.tsx`, which
// ships this mechanism for a single arm:
//
//   1. The acknowledgement is a *mutation parameter*, never state read at call
//      time — "a component that always sent `true` would turn the gate into a
//      dismissible banner in JSON".
//   2. An acknowledgement is of a *specific consequence*, so an edit clears it.
//
// Rule 2 is deliberately NARROWER here than on that form, and the reason is
// recorded so it is not "fixed" back: every editable field there is
// consequence-bearing, so "any edit clears" and "consequence-bearing edit
// clears" are the same rule. This form has consequence-NEUTRAL fields. Neither
// guard reads `name`, `category` or `poll_interval` — the starvation arm reads
// the user's folder-sorting mailboxes and the overlap arm reads the paperless
// mail config — so clearing on those edits would protect nothing while
// re-opening the loop through the UI.
//
// What DOES invalidate an acknowledgement is the credential changing, because
// the overlap answer is computed from the credential's paperless instance. A
// user shown the overlap card who concludes "wrong server", fixes the
// credential and re-posts would otherwise carry an answer given about a
// different instance — and the server re-validates nothing, since both guards
// are skipped whenever their flag is present. **This reset is a client-side
// honesty measure, not a security control.**
import { starvationWarning, type StarvationWarning } from "../signals/externalConsumerWarning";
import {
  paperlessOverlapWarning,
  type PaperlessOverlapWarning,
} from "../email/paperlessOverlapWarning";

export type ConsentArm = "starvation" | "overlap";

/** At most one card is shown at a time — the server checks the starvation arm
 *  first and unconditionally, so the arms arrive in sequence, never together. */
export type ShownCard =
  | { arm: "starvation"; warning: StarvationWarning }
  | { arm: "overlap"; warning: PaperlessOverlapWarning };

export interface ConsentState {
  acked: { starvation: boolean; overlap: boolean };
  shown: ShownCard | null;
  /** The paperless credential row's `updated_at`, `null` when there is no row,
   *  `"unknown"` when the presence read itself failed. Tracked as an identity
   *  rather than a boolean so a credential being *replaced* is visible — a
   *  presence flag cannot see that, and replacement is exactly the case that
   *  makes an acknowledgement stale. */
  credentialAt: string | null | "unknown";
}

export const initialConsentState: ConsentState = {
  acked: { starvation: false, overlap: false },
  shown: null,
  credentialAt: "unknown",
};

export type ConsentAction =
  /** An attempt was submitted — clears any card so the UI cannot show a stale
   *  refusal while the next request is in flight. */
  | { type: "submitted" }
  /** The request was refused; `error` is the raw ApiError. Returns unchanged if
   *  neither parser recognises it — an unrecognised failure is the view's
   *  generic-error path and must not disturb consents already given. */
  | { type: "refused"; error: unknown }
  /** The user pressed "Connect anyway" on the card currently shown. */
  | { type: "acknowledged" }
  | { type: "succeeded" }
  /** The URL or the source type changed, or a new preview was run. */
  | { type: "inputsChanged" }
  /** The credential-presence read resolved. */
  | { type: "credentialRead"; at: string | null | "unknown" };

const CLEARED = { starvation: false, overlap: false } as const;

export function consentReducer(state: ConsentState, action: ConsentAction): ConsentState {
  switch (action.type) {
    case "submitted":
      return { ...state, shown: null };

    case "refused": {
      const starvation = starvationWarning(action.error);
      if (starvation) {
        return { ...state, shown: { arm: "starvation", warning: starvation } };
      }
      const overlap = paperlessOverlapWarning(action.error);
      if (overlap) {
        return { ...state, shown: { arm: "overlap", warning: overlap } };
      }
      // Neither parser matched: a 5xx, or a 422 wearing an unrecognised code.
      // `acked` and `shown` are BOTH left alone so pressing again retries the
      // same step — a network blip is not a reason to make someone re-answer
      // consents they have already given.
      return state;
    }

    case "acknowledged": {
      if (!state.shown) return state;
      return {
        ...state,
        acked: { ...state.acked, [state.shown.arm]: true },
        shown: null,
      };
    }

    case "succeeded":
      return { ...state, acked: { ...CLEARED }, shown: null };

    case "inputsChanged":
      return { ...state, acked: { ...CLEARED }, shown: null };

    case "credentialRead": {
      if (action.at === state.credentialAt) return state;
      // Any change at all — absent → present, present → absent, or one
      // timestamp to a different one. The last is the case a presence boolean
      // cannot see, and the one that matters most.
      //
      // The first read of the session is excluded: `credentialAt` starts at
      // `"unknown"` and its resolution is not a change the user made, so it
      // must not clear consents given before the query settled.
      const isFirstResolution = state.credentialAt === "unknown";
      return {
        ...state,
        credentialAt: action.at,
        acked: isFirstResolution ? state.acked : { ...CLEARED },
        shown: isFirstResolution ? state.shown : null,
      };
    }

    default:
      return state;
  }
}

/** The body flags for the next request: exactly what is `true` in `acked`.
 *
 *  The single source of truth for what gets sent. A view that assembled this
 *  inline would be free to drift from the rule, which is how the two-arm path
 *  became an infinite loop in the plan's first draft — dropping
 *  `acknowledge_external_consumer` on the second re-post re-triggers the arm the
 *  user already answered, because the server checks it first and unconditionally
 *  on every request. */
export function consentFlags(state: ConsentState): {
  acknowledge_external_consumer?: boolean;
  acknowledge_paperless_overlap?: boolean;
} {
  return {
    ...(state.acked.starvation ? { acknowledge_external_consumer: true } : {}),
    ...(state.acked.overlap ? { acknowledge_paperless_overlap: true } : {}),
  };
}
