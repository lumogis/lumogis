// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Public forgot-password client (LUM-65). Uses bare `fetch` rather than
// `ApiClient` on purpose: every caller here is unauthenticated by definition —
// someone who cannot sign in — so there is no access token to attach and no
// session to refresh.

export interface PasswordResetStatus {
  email_reset_enabled: boolean;
}

/** The ALTCHA v2 challenge, passed through from the server verbatim. */
export interface AltchaChallenge {
  parameters: Record<string, unknown>;
  signature: string;
}

/**
 * Human sentences for the machine codes this surface can return.
 *
 * `detailOf` used to fall back to the raw `code`, so these pages rendered
 * `email_reset_disabled` and `altcha_invalid` as user-facing text — to exactly
 * the people least able to interpret it, since everyone reaching this page is
 * locked out and not necessarily technical (LUM-65 P2 #10).
 *
 * An unrecognised code now yields the caller's fallback rather than the code
 * itself: a sentence that is vaguer but readable beats a precise token nobody
 * can act on. Codes stay available to tests and logs via the thrown error's
 * `code` property.
 */
const CODE_MESSAGES: Record<string, string> = {
  email_reset_disabled:
    "Password reset by email is not set up on this Lumogis. Ask an admin to reset your password.",
  altcha_invalid:
    "The verification check did not complete. Please reload the page and try again.",
  altcha_not_configured:
    "The verification check is not set up on this Lumogis. Ask an admin to reset your password.",
};

/** Error carrying the server's machine code alongside a readable message. */
export class PasswordResetError extends Error {
  readonly code?: string;

  constructor(message: string, code?: string) {
    super(message);
    this.name = "PasswordResetError";
    this.code = code;
  }
}

interface Detail {
  message: string;
  code?: string;
}

async function detailOf(res: Response, fallback: string): Promise<Detail> {
  try {
    const body = (await res.json()) as { detail?: unknown };
    // A string detail is already written for a human (the rate limiters).
    if (typeof body.detail === "string") return { message: body.detail };
    if (body.detail && typeof body.detail === "object") {
      const d = body.detail as { message?: unknown; code?: unknown };
      if (typeof d.message === "string") return { message: d.message, code: codeOf(d) };
      const code = codeOf(d);
      if (code) return { message: CODE_MESSAGES[code] ?? fallback, code };
    }
  } catch {
    /* keep fallback */
  }
  return { message: fallback };
}

function codeOf(d: { code?: unknown }): string | undefined {
  return typeof d.code === "string" ? d.code : undefined;
}

export async function getPasswordResetStatus(): Promise<PasswordResetStatus> {
  const res = await fetch("/api/v1/password-reset/status");
  if (!res.ok) {
    // Treat any failure as "not available": the page then shows the
    // ask-an-admin guidance, which is true whenever we cannot confirm
    // otherwise, and never a form that cannot work.
    return { email_reset_enabled: false };
  }
  return (await res.json()) as PasswordResetStatus;
}

export async function getAltchaChallenge(): Promise<AltchaChallenge> {
  const res = await fetch("/api/v1/altcha/challenge");
  if (!res.ok) {
    const d = await detailOf(res, "Verification is unavailable");
    throw new PasswordResetError(d.message, d.code);
  }
  return (await res.json()) as AltchaChallenge;
}

export class PasswordResetDisabledError extends Error {}

/**
 * Ask for a reset link.
 *
 * Resolves for both known and unknown addresses — that is the contract, not an
 * oversight. Callers must render the same confirmation either way; branching on
 * the result would rebuild in the browser the account-enumeration oracle the
 * server goes to some trouble to avoid.
 */
export async function requestPasswordReset(email: string, altcha: string): Promise<void> {
  const res = await fetch("/api/v1/password-reset/request", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, altcha }),
  });
  if (res.status === 503) {
    const d = await detailOf(res, "Email password reset is not available on this Lumogis");
    throw new PasswordResetDisabledError(d.message);
  }
  if (!res.ok) {
    const d = await detailOf(res, "Could not send the reset email");
    throw new PasswordResetError(d.message, d.code);
  }
}

export async function redeemPasswordReset(
  token: string,
  newPassword: string,
): Promise<void> {
  const res = await fetch("/api/v1/password-reset/redeem", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ token, new_password: newPassword }),
  });
  if (!res.ok) {
    const d = await detailOf(res, "Reset link is invalid or expired");
    throw new PasswordResetError(d.message, d.code);
  }
}
