// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-770 — copy for a failed mailbox save.
//
// The setup form's outer catch used to do:
//
//     setError(err instanceof ApiError ? String(err.detail) : "Could not add mailbox")
//
// `ApiError.detail` is the response body AS A STRING — for this app's FastAPI
// shape, `safeReadDetail` returns `JSON.stringify(body)`. So a user was shown
// `{"detail":{"error":"bad_port","code":"bad_port"}}`. Worse, where a route
// passes an exception message through, they were shown it verbatim, and
// credential-layer messages name hosts, payload fields and user_ids.
//
// ADR-197 established the opposite rule for the SIBLING catch three lines away
// (`sendOptInMessage.ts`): key on a closed set of `code` values, never touch
// `err.message` or `err.detail`, prove it with a DOM sentinel test. This catch
// renders too, and predates the rule. Same module shape, same rule, same test.

import { ApiError } from "../../api/client";

/**
 * Why the mailbox couldn't be saved.
 *
 * Keyed on `ApiError.code` against a closed set. Anything unrecognised — and
 * anything that is not an `ApiError` at all — falls back to a fixed sentence.
 * **No server-supplied string is ever returned from this function.**
 */
export function describeMailboxSaveFailure(
  err: unknown,
  fallback = "Couldn't add the mailbox. Check the details and try again.",
): string {
  const code = err instanceof ApiError ? err.code : undefined;
  switch (code) {
    case "bad_mailbox_label":
      return "That mailbox label can't be used — use lowercase letters, numbers, dashes or underscores.";
    case "bad_port":
      return "That port can't be used. Check the port your provider gave you.";
    case "smtp_host_required":
      return (
        "Sending needs this provider's sending server address and port — " +
        "Lumogis doesn't recognise this mailbox's provider."
      );
    case "app_password_required":
      return "A sending password is needed, and none was entered.";
    case "rate_limited":
      return "Too many attempts just now. Wait a minute and try again.";
    case "not_found":
      return "That mailbox no longer exists. Reload the page and try again.";
    case "imap_not_configured":
      return "Lumogis has no sign-in stored for this mailbox.";
    case "credential_unavailable":
      return "Lumogis couldn't store this mailbox's credentials. Ask your Lumogis operator to check the server.";
    case "egress_blocked":
      return (
        "This mailbox's server isn't on the allowed-connections list for this " +
        "deployment. Your credentials are fine; ask your Lumogis operator to allow the host."
      );
    case "mailbox_unreachable":
      return "Lumogis couldn't reach that mail server just now. This usually clears up on its own — try again.";
    default:
      return fallback;
  }
}
