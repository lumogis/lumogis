// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Browser side of the ALTCHA proof of work (LUM-65).
//
// Pinned to `altcha@^3`, which speaks the **v2** protocol — the same branch as
// the server's `altcha==2.1.0`, whose top-level `create_challenge` /
// `verify_solution` are v2. The widget major and the Python branch are a single
// wire contract: `altcha@2` speaks v1 and would produce payloads this server
// cannot verify, with no type error to warn you.
//
// We call the library directly rather than mounting the `<altcha-widget>` web
// component. The widget owns its own challenge fetch, form field, and visible
// checkbox; here the whole interaction is one button on a page with one input,
// so a checkbox that must also be ticked is a second thing to fail at for
// someone already locked out of their account.

import { sha, solveChallenge } from "altcha/lib";

import type { AltchaChallenge } from "../../api/passwordReset";

/**
 * Solve `challenge` and return the base64 payload the server expects.
 *
 * The payload is `base64(JSON({challenge, solution}))` — the challenge is
 * echoed back because the server verifies its own HMAC signature over the
 * parameters before checking the work, so a tampered challenge is rejected
 * without trusting anything the browser says about it.
 *
 * `sha.deriveKey` matches the server's `algorithm: "SHA-256"`. Note the shape:
 * the library exports KDF *namespaces* (`sha`, `pbkdf2`, `scrypt`, `argon2id`),
 * each holding a `deriveKey` — passing the namespace itself fails at runtime
 * with `deriveKey is not a function`, not at compile time.
 */
export async function solveAltchaChallenge(challenge: AltchaChallenge): Promise<string> {
  const solution = await solveChallenge({
    challenge: challenge as never,
    deriveKey: sha.deriveKey,
  });
  if (!solution) {
    throw new Error("Verification could not be completed — please try again");
  }
  const payload = JSON.stringify({ challenge, solution });
  return btoa(payload);
}
