// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-846 — shared copy and validation for credential forms that carry a secret.
//
// ## Why this exists at all
//
// `put_payload` is **whole-payload replacement** — `ciphertext =
// EXCLUDED.ciphertext` (`services/connector_credentials.py`), with no merge
// against the stored payload. A form that omits a field on edit does not
// "leave it alone"; it **deletes it**.
//
// Three forms shipped the same "leave blank to keep existing" gesture against
// that backend, and none of them could keep anything:
//
//   if (pass.length > 0) payload.password = pass;
//   else if (!isEdit) payload.password = "";
//
// The user opens the connector to fix a typo in the URL, never touches the
// password field (which looks empty because the form never renders stored
// values — the deliberate no-decrypt posture), presses Save, and the stored
// secret is gone. The next poll fails with a credential error, which reads as
// *"the server rejected us"* rather than *"we deleted your password."*
//
// ## Why one module rather than three copies
//
// This is one rule about one backend behaviour. Three copies of it drift, and
// the drift is silent — which is exactly how the bug reached three forms in the
// first place. The copy lives here so a fourth form gets it by importing rather
// than by remembering.
//
// "Leave blank to keep" becomes honest only once the backend can merge, or once
// field-presence metadata (LUM-834 G4) lets the form send an explicit
// keep-existing marker instead of an omission. Neither exists yet.

/**
 * Shown on edit, wherever a required secret is collected.
 *
 * Deliberately says what the save *does*, not what the field wants. "Password
 * is required" is true of the create case too and explains nothing; the thing
 * the user cannot otherwise know is that saving rewrites the whole credential.
 */
export const REPLACE_NOTICE =
  "Saving replaces the whole credential — enter the secret again, even if it has not changed.";

/**
 * The inline error when a required secret is missing.
 *
 * Different on edit, because the *reason* differs: on create the field is
 * simply required; on edit, submitting without it would destroy something.
 */
export function secretRequiredMessage(isEdit: boolean, label: string): string {
  return isEdit
    ? `Re-enter the ${label} — saving replaces the whole credential, so leaving it blank would delete the stored one.`
    : `The ${label} is required.`;
}

/**
 * True when a submitted secret counts as entered.
 *
 * Whitespace does not count. A `required` attribute accepts `"   "` happily,
 * and every loader in this codebase rejects a whitespace-padded secret — so a
 * form that let one through would save a credential that can never
 * authenticate, which is the failure mode this whole module exists to prevent.
 */
export function isSecretEntered(raw: string): boolean {
  return raw.trim().length > 0;
}
