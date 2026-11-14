// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-759 — copy for a failed "let Lumogis send as me" opt-in.
// LUM-769 — shared with the mailbox card, which needs a second vocabulary for
// the 200-shaped outcomes and a different framing sentence.
//
// Its own module, mirroring the sibling `pollHealthMessage.ts`, for two reasons:
// the lint rule `react-refresh/only-export-components` forbids exporting a
// non-component from a component file (zero-warning gate), and this is the
// second copy-mapping function in this directory — putting it beside the first
// is where a reader will look for it.
//
// **One module owns this vocabulary.** Two surfaces consume it (the setup form
// and the mailbox card) and a new code must be added HERE, not in either
// consumer — a second vocabulary for one condition is LUM-760's whole complaint.

import { ApiError } from "../../api/client";

/**
 * The setup form's framing: the mailbox has just been created, so the useful
 * thing to say first is that the connection itself worked.
 */
export const SEND_OPT_IN_PREFIX = "Mailbox connected. Sending isn't on yet — ";

/**
 * The mailbox card's framing. A mailbox connected weeks ago does not need to be
 * told it is connected, and "Mailbox connected" would read as though something
 * had just happened.
 */
export const SEND_CARD_PREFIX = "Sending isn't on — ";

/**
 * The card's framing when a *disable* failed (LUM-769). Every string below is
 * about turning sending ON, so a failed turn-off routed through the opt-in
 * prefix would have read "Sending isn't on yet — you can try again later" to a
 * user who had just tried to turn it off and still has it on.
 */
export const SEND_DISABLE_PREFIX = "Sending is still on — ";

/**
 * Why the send opt-in didn't take (LUM-759).
 *
 * Keyed on the route's `code`, deliberately mirroring `pollHealthMessage.ts` so
 * the same condition reads the same way in the health banner and here.
 *
 * **Never renders `err.message` or `err.detail`.** `ApiError.message` is
 * `HTTP ${status}: ${detail}`, and for an object body `detail` is
 * `JSON.stringify(body)` — so the idiomatic `err instanceof Error ? err.message`
 * fallback would put a serialised error envelope on screen, and on any route
 * that passes an exception message through, a credential-layer string naming a
 * host or a payload field. The last resort is the fixed generic line.
 *
 * **Each string stands alone** (LUM-769). They used to be lowercase
 * continuations of the prefix's em-dash, which meant a surface passing a
 * different framing rendered half of them starting mid-sentence.
 */
export function describeSendOptInFailure(err: unknown, prefix = SEND_OPT_IN_PREFIX): string {
  const code = err instanceof ApiError ? err.code : undefined;
  switch (code) {
    case "mailbox_unreachable":
      return (
        prefix +
        "Lumogis couldn't reach the mail server just now. This usually clears " +
        "up on its own; you can try again later."
      );
    case "egress_blocked":
      return (
        prefix +
        "This mailbox's server isn't on the allowed-connections list for this " +
        "deployment. Your credentials are fine; ask your Lumogis operator to " +
        "allow the host."
      );
    case "oauth_unavailable":
      return (
        prefix +
        "Sign-in for this provider isn't configured on this Lumogis. Ask your " +
        "Lumogis operator."
      );
    case "credential_unavailable":
      return (
        prefix +
        "Lumogis couldn't read this mailbox's stored credentials. Ask your " +
        "Lumogis operator to check the server."
      );
    case "imap_reauth_required":
      return prefix + "This mailbox's sign-in has expired. Reconnect it, then try again.";
    // LUM-769: the body, not only the prefix, is per-surface. "They need
    // correcting first" was actionable in the setup form, where the fields are
    // on screen; on the card there is no way to edit stored IMAP settings, so it
    // points at the affordance that does exist (LUM-701's reconnect).
    case "imap_settings_invalid":
      return (
        prefix +
        "This mailbox's stored settings can't be used. Reconnect the mailbox to " +
        "correct them."
      );
    case "imap_not_configured":
      return prefix + "Lumogis has no sign-in stored for this mailbox.";
    // --- LUM-769 / Decision 2d ------------------------------------------- //
    // These three are NOT new conditions. All three are reachable in shipped
    // code today and have always rendered the generic fallback: the setup form's
    // three SMTP inputs carry no `required` attribute (its IMAP fields all do)
    // and it trims a whitespace host to `undefined`, while the opt-in route is
    // rate-limited at 10/60s. A code the server returns about a form the user is
    // looking at must not be answered with a shrug.
    case "smtp_host_required":
      return (
        prefix +
        "This mailbox's provider isn't one Lumogis knows, so it needs the " +
        "sending server's address and port."
      );
    case "app_password_required":
      return prefix + "A sending password is needed, and none was entered.";
    case "rate_limited":
      return prefix + "Too many attempts just now. Wait a minute and try again.";
    default:
      return prefix + "You can try again later.";
  }
}

/**
 * Why the pre-save connect-test failed (LUM-769).
 *
 * A separate export because these arrive as **200s that never throw**:
 * `set_email_smtp` runs a live SMTP connect-test and reports its failure as
 * `{ok:false, enabled:false, tested:true, error}`, so `describeSendOptInFailure`
 * — which branches on `ApiError.code` — is never reached for them.
 *
 * The set is closed and small because the adapter raises exactly three codes,
 * one of which never arrives here:
 *
 * - `smtp_auth`         — the sending password was refused.
 * - `smtp_oauth_denied` — an OAuth mailbox refused by its provider (LUM-771).
 * - `smtp_connect`      — the submission server was unreachable.
 *
 * **`egress_blocked` is deliberately absent.** `verify_smtp_connection` returns
 * `(True, False, None)` for an egress block, so the code is `null` and the case
 * arrives as the soft-save shape — enabled, unverified — not as an error. A
 * branch for it here could never fire.
 */
export function describeSendVerifyFailure(
  errorCode: string | null | undefined,
  prefix = SEND_CARD_PREFIX,
): string {
  switch (errorCode) {
    case "smtp_auth":
      return (
        prefix +
        "That mailbox password wasn't accepted for sending. If this mailbox uses " +
        "a separate sending password, enter it below."
      );
    // LUM-771/ADR-197: an OAuth mailbox has NO password to correct, so this must
    // never read like a rejected password and must never reveal a password
    // field. The fix lives with whoever administers the email account — for
    // Outlook that is a Microsoft 365 tenant setting — and NOT with the Lumogis
    // operator, who cannot change it. Naming the wrong fix-owner is LUM-760's
    // complaint, and this is the path it would have survived on.
    case "smtp_oauth_denied":
      return (
        prefix +
        "This mailbox's provider isn't allowing Lumogis to send. Whoever manages " +
        "the email account needs to turn on SMTP sending for it."
      );
    case "smtp_connect":
      return (
        prefix +
        "Lumogis couldn't reach the sending server. This is usually temporary — " +
        "try again."
      );
    default:
      return prefix + "The sending test didn't pass. You can try again later.";
  }
}
