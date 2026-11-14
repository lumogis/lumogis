// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-65 P2 #10 — the public reset client must never render a machine code as
// user-facing text. Everyone reaching these pages is locked out and not
// necessarily technical; `email_reset_disabled` is not a sentence.

import { afterEach, describe, expect, it, vi } from "vitest";
import {
  getAltchaChallenge,
  PasswordResetDisabledError,
  PasswordResetError,
  redeemPasswordReset,
  requestPasswordReset,
} from "../../src/api/passwordReset";

function respond(body: unknown, status: number): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function mockFetch(body: unknown, status: number) {
  const fn = vi.fn().mockResolvedValue(respond(body, status));
  vi.stubGlobal("fetch", fn);
  return fn;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("machine codes never reach the user", () => {
  it("maps altcha_invalid to a sentence, and keeps the code on the error", async () => {
    mockFetch({ detail: { code: "altcha_invalid" } }, 400);

    const err = await requestPasswordReset("a@example.com", "sol").catch((e) => e);

    expect(err).toBeInstanceOf(PasswordResetError);
    expect(err.message).toContain("verification check did not complete");
    expect(err.message).not.toContain("altcha_invalid");
    // Still available to tests and logs — readable for humans, precise for code.
    expect(err.code).toBe("altcha_invalid");
  });

  it("maps altcha_not_configured on the challenge endpoint", async () => {
    mockFetch({ detail: { code: "altcha_not_configured" } }, 503);

    const err = await getAltchaChallenge().catch((e) => e);

    expect(err.message).toContain("not set up on this Lumogis");
    expect(err.message).not.toContain("altcha_not_configured");
  });

  it("maps email_reset_disabled and still uses the disabled error type", async () => {
    mockFetch({ detail: { code: "email_reset_disabled" } }, 503);

    const err = await requestPasswordReset("a@example.com", "sol").catch((e) => e);

    // The 503 branch drives page-level routing, so the type must not regress.
    expect(err).toBeInstanceOf(PasswordResetDisabledError);
    expect(err.message).toContain("Ask an admin");
    expect(err.message).not.toContain("email_reset_disabled");
  });

  it("falls back to the caller's sentence for an unrecognised code", async () => {
    mockFetch({ detail: { code: "some_future_code" } }, 400);

    const err = await redeemPasswordReset("tok", "pw").catch((e) => e);

    // Vaguer but readable beats a precise token nobody can act on.
    expect(err.message).toBe("Reset link is invalid or expired");
    expect(err.message).not.toContain("some_future_code");
    expect(err.code).toBe("some_future_code");
  });

  it("passes a string detail through — the rate limiters already write prose", async () => {
    mockFetch({ detail: "too many requests; try again in a minute" }, 429);

    const err = await requestPasswordReset("a@example.com", "sol").catch((e) => e);

    expect(err.message).toBe("too many requests; try again in a minute");
  });

  it("uses the fallback when the body is not JSON at all", async () => {
    const fn = vi.fn().mockResolvedValue(new Response("<html>502</html>", { status: 502 }));
    vi.stubGlobal("fetch", fn);

    const err = await redeemPasswordReset("tok", "pw").catch((e) => e);

    expect(err.message).toBe("Reset link is invalid or expired");
  });
});
