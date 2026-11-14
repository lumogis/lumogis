// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
import { describe, expect, it } from "vitest";
import { pollHealthMessage } from "../../src/features/signals/pollHealthMessage";

/**
 * LUM-633 — the codes exist so the copy can differ, because the right user
 * action differs. A single message was actively harmful: it told everyone
 * "Lumogis can't sign in to this mailbox", so a user whose mailbox was blocked
 * by an operator egress policy would go and re-enter a password that was fine.
 */
describe("pollHealthMessage", () => {
  it("tells a credential problem apart from a policy block", () => {
    const credential = pollHealthMessage("credential");
    const blocked = pollHealthMessage("egress_blocked");

    expect(credential).not.toEqual(blocked);
    // The blocked case must NOT send the user to fix credentials.
    expect(blocked.toLowerCase()).toContain("credentials are fine");
    expect(blocked.toLowerCase()).toContain("operator");
    expect(credential.toLowerCase()).toMatch(/reconnect|password/);
  });

  it("names a fix-owner for config without claiming the user entered anything", () => {
    // LUM-760. Since LUM-757, `config` covers BOTH settings a household member
    // typed and an operator/provider problem they could not have caused — a
    // missing client id, an unknown provider, an `invalid_client`. The old copy
    // said "as entered … the settings need correcting", which told someone they
    // had mistyped something they never typed.
    //
    // This surface cannot tell the two apart: it receives only the code, and no
    // auth field reaches it from `GET /sources`. So the copy must name both
    // owners and assert neither.
    const config = pollHealthMessage("config").toLowerCase();

    expect(config).toContain("operator"); // the owner the old copy never named
    expect(config).toContain("if you entered them yourself"); // conditional, not asserted
    // The specific phrasing that made the old copy wrong.
    expect(config).not.toContain("as entered");
  });

  it("matches egress_blocked's shape: say who can fix it", () => {
    // `egress_blocked` was already right — "ask your Lumogis operator to allow
    // the host" — and was the comparison the ticket used. Both now name an owner.
    for (const code of ["config", "egress_blocked"]) {
      expect(pollHealthMessage(code).toLowerCase()).toContain("operator");
    }
  });

  it("does not promise self-healing for a config error", () => {
    // `config` exists precisely because "usually clears up on its own" is a lie
    // for a bad port or a plaintext-to-remote policy violation.
    const config = pollHealthMessage("config");
    expect(config.toLowerCase()).toContain("won't fix itself");
    expect(config.toLowerCase()).not.toContain("clears up on its own");
  });

  it("says a network problem usually clears on its own", () => {
    expect(pollHealthMessage("network").toLowerCase()).toContain("clears up on its own");
  });

  it("falls back safely for a code this client does not know", () => {
    // The load-bearing one: the server can emit a new code before the client
    // knows it. Rendering a raw code — or nothing — at a user is worse than a
    // vague sentence.
    const unknown = pollHealthMessage("some_future_code");

    expect(unknown).toBeTruthy();
    expect(unknown).not.toContain("some_future_code");
    expect(unknown.toLowerCase()).toContain("operator");
  });

  // --- LUM-777: split by auth method where `GET /sources` knows it ---------- //
  //
  // The exact strings are inlined (`toBe`), not matched by substring: the copy IS
  // the behaviour here. Negative checks use word-boundary regexes so "supported"
  // never matches `port`.

  const CREDENTIAL_OAUTH =
    "Lumogis can't sign in to this mailbox with your provider account. The access " +
    "may have been revoked or may have expired, or the provider may have turned off " +
    "mail access for this account. Reconnect the mailbox; if that doesn't help, ask " +
    "whoever manages the account.";
  const CREDENTIAL_APP_PASSWORD =
    "Lumogis can't sign in to this mailbox. The app password may have been revoked " +
    "or changed — update it, or reconnect the mailbox.";
  const CREDENTIAL_UNKNOWN =
    "Lumogis can't sign in to this mailbox. Reconnect it, or check the app password " +
    "hasn't been revoked.";
  const CONFIG_OAUTH =
    "this mailbox's sign-in with the provider isn't set up correctly on this " +
    "Lumogis, and this won't fix itself. Ask your Lumogis operator.";
  const CONFIG_APP_PASSWORD =
    "this mailbox's saved connection settings can't be used, and this won't fix " +
    "itself. One of them needs correcting — the server, port, encryption or password.";
  const CONFIG_UNKNOWN =
    "this mailbox's connection settings can't be used, and this won't fix itself. " +
    "If you entered them yourself, a setting like the port or encryption needs " +
    "correcting. If you connected this mailbox by signing in with the provider, the " +
    "sign-in isn't set up correctly on this Lumogis — ask your Lumogis operator.";

  it("credential: an OAuth mailbox is not told about a password", () => {
    const msg = pollHealthMessage("credential", "oauth");

    expect(msg).toBe(CREDENTIAL_OAUTH);
    expect(msg).not.toMatch(/\bpassword\b/i);
  });

  it("credential: an app-password mailbox is told to update the password", () => {
    expect(pollHealthMessage("credential", "app_password")).toBe(CREDENTIAL_APP_PASSWORD);
  });

  it("credential: unknown is byte-identical to the pre-LUM-777 copy", () => {
    // null and undefined are BOTH unknown (an older backend omits the key).
    expect(pollHealthMessage("credential")).toBe(CREDENTIAL_UNKNOWN);
    expect(pollHealthMessage("credential", null)).toBe(CREDENTIAL_UNKNOWN);
    expect(pollHealthMessage("credential", undefined)).toBe(CREDENTIAL_UNKNOWN);
  });

  it("config: an OAuth mailbox names the operator, not settings the user typed", () => {
    const msg = pollHealthMessage("config", "oauth");

    expect(msg).toBe(CONFIG_OAUTH);
    expect(msg).not.toMatch(/\bport\b/i);
    expect(msg).not.toMatch(/\bencryption\b/i);
    expect(msg).not.toMatch(/if you entered them yourself/i);
  });

  it("config: an app-password mailbox names the settings, not the operator", () => {
    const msg = pollHealthMessage("config", "app_password");

    expect(msg).toBe(CONFIG_APP_PASSWORD);
    expect(msg).not.toMatch(/\boperator\b/i);
    expect(msg).not.toMatch(/as entered/i);
  });

  it("config: unknown keeps naming both owners (ADR-195's standing rule)", () => {
    expect(pollHealthMessage("config", null)).toBe(CONFIG_UNKNOWN);
    expect(pollHealthMessage("config")).toBe(CONFIG_UNKNOWN);
  });

  it("other codes ignore the auth method", () => {
    for (const code of ["egress_blocked", "network", "some_future_code"]) {
      const bare = pollHealthMessage(code);
      expect(pollHealthMessage(code, "oauth")).toBe(bare);
      expect(pollHealthMessage(code, "app_password")).toBe(bare);
    }
  });
});
