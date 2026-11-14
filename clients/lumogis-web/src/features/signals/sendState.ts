// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-769 / LUM-774 — the mailbox card's send state, and the ordering that
// derives it.
//
// Its own module for the same reason `pollHealthMessage.ts` and
// `sendOptInMessage.ts` are: `react-refresh/only-export-components` forbids
// exporting a non-component from a component file, and this repo runs the lint
// gate at zero warnings. Extracting it here is also what makes the ordering
// below testable at all — see `deriveSendState`.

/**
 * `unknown` is reachable and must be rendered: the credentials query can fail,
 * and claiming "off" for a mailbox whose state we could not read would invite a
 * user to turn on something that is already on.
 *
 * `unverified` is the egress soft-save — saved, but the connect-test could not
 * run. It is owned by the view, not derived from the credential row, which is
 * identical whether or not the test passed.
 */
export type SendState = "on" | "off" | "unverified" | "unknown";

/**
 * Derive a mailbox's send state from the two things that know about it.
 *
 * Extracted (LUM-774) because the ordering is load-bearing and was **not
 * provable in place**: a mutation letting `softSaved` outrank the credential
 * passes every rendered test in the suite, because it only misbehaves when the
 * two DISAGREE — and nothing a user can click both drops the credential and
 * refetches it in the same session, so no render fixture can reach that state
 * without being contorted into something less true than production.
 *
 * The rule: **`softSaved` may only ever REFINE a live credential** into "not yet
 * verified". It must never assert one. A mailbox whose SMTP row was removed
 * elsewhere — another tab, another device, an admin action — reads "off".
 * Saying sending is on when it is off is the one thing this surface exists to
 * prevent.
 */
export function deriveSendState(input: {
  /** The credentials query failed or is still loading — do not guess "off". */
  credentialsUnavailable: boolean;
  /** An `smtp` credential row exists for this mailbox. */
  hasSmtpCredential: boolean;
  /** This session saw a soft-save (persisted, but the connect-test could not run). */
  softSaved: boolean;
}): SendState {
  if (input.credentialsUnavailable) return "unknown";
  if (!input.hasSmtpCredential) return "off";
  return input.softSaved ? "unverified" : "on";
}
