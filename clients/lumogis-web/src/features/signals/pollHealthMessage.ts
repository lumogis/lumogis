// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
import type { MailboxAuthMethod } from "../../api/emailSources";

/**
 * User-facing copy for a `last_poll_error_code` (LUM-633).
 *
 * Each code implies a DIFFERENT action, which is the whole reason the codes
 * exist. `credential` is the only one the user can fix themselves;
 * `egress_blocked` is an operator policy decision and no amount of
 * password-re-entry will move it; `network` and `config` differ on whether
 * waiting helps at all.
 *
 * The default branch is load-bearing: the server may start emitting a code
 * this client predates, and rendering a raw code (or nothing) at a user would
 * be worse than a slightly vague sentence.
 *
 * `authMethod` (LUM-777) names the right fix-owner for `credential` and
 * `config` when `GET /sources` knows how the mailbox signs in. `null` and
 * `undefined` both mean UNKNOWN and keep the hedged copy that names every owner
 * (ADR-195's standing rule: a code that spans two fix-owners must name both).
 * The other codes ignore it.
 */
export function pollHealthMessage(code: string, authMethod?: MailboxAuthMethod | null): string {
  switch (code) {
    case "credential":
      if (authMethod === "oauth") {
        return (
          "Lumogis can't sign in to this mailbox with your provider account. The " +
          "access may have been revoked or may have expired, or the provider may " +
          "have turned off mail access for this account. Reconnect the mailbox; " +
          "if that doesn't help, ask whoever manages the account."
        );
      }
      if (authMethod === "app_password") {
        return (
          "Lumogis can't sign in to this mailbox. The app password may have been " +
          "revoked or changed — update it, or reconnect the mailbox."
        );
      }
      return (
        "Lumogis can't sign in to this mailbox. Reconnect it, or check the app " +
        "password hasn't been revoked."
      );
    case "egress_blocked":
      return (
        "this mailbox's server isn't on the allowed-connections list for this " +
        "deployment, so Lumogis can't reach it. Your credentials are fine — ask " +
        "your Lumogis operator to allow the host."
      );
    case "network":
      return (
        "Lumogis couldn't reach the mail server. This usually clears up on its " +
        "own; if it doesn't, check the server address is still correct."
      );
    case "config":
      // LUM-777: since `GET /sources` publishes the auth method, the split LUM-760
      // said did not exist now does. `config` still spans two fix-owners (settings
      // the user typed, or an operator/provider problem such as a missing
      // `LUMOGIS_OAUTH_*_CLIENT_ID`), so UNKNOWN keeps the copy that names BOTH
      // and asserts neither; a known method names the one owner.
      if (authMethod === "oauth") {
        return (
          "this mailbox's sign-in with the provider isn't set up correctly on " +
          "this Lumogis, and this won't fix itself. Ask your Lumogis operator."
        );
      }
      if (authMethod === "app_password") {
        return (
          "this mailbox's saved connection settings can't be used, and this " +
          "won't fix itself. One of them needs correcting — the server, port, " +
          "encryption or password."
        );
      }
      return (
        "this mailbox's connection settings can't be used, and this won't fix " +
        "itself. If you entered them yourself, a setting like the port or " +
        "encryption needs correcting. If you connected this mailbox by signing " +
        "in with the provider, the sign-in isn't set up correctly on this " +
        "Lumogis — ask your Lumogis operator."
      );
    default:
      return (
        "Lumogis couldn't sync this mailbox. If it doesn't clear on its own, ask " +
        "your Lumogis operator to check it."
      );
  }
}
