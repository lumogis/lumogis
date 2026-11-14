// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-65 Pass 1 + Pass 2. Mounted OUTSIDE <RequireAuth> (a sibling of
// /invite): everything under the authenticated shell renders the login form
// for an anonymous visitor, and the one visitor this page exists for is
// precisely the one who cannot log in.
//
// The page adapts to `email_reset_enabled`. When email reset is off — no SMTP,
// no proven Send test, no public origin, or no Altcha key — it shows the
// Pass-1 guidance instead of a form that cannot work.

import { useEffect, useState, type FormEvent, type JSX } from "react";

import {
  PasswordResetDisabledError,
  getAltchaChallenge,
  getPasswordResetStatus,
  requestPasswordReset,
} from "../../api/passwordReset";
import { solveAltchaChallenge } from "./altcha";

type Status = "loading" | "enabled" | "disabled";

export function ForgotPasswordPage(): JSX.Element {
  const [status, setStatus] = useState<Status>("loading");
  const [email, setEmail] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [submitted, setSubmitted] = useState(false);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      // Fail closed to the Pass-1 guidance, never to the loading placeholder.
      //
      // `getPasswordResetStatus` swallows a non-2xx, but not a rejected fetch
      // (offline, DNS, TLS, a proxy aborting the request) and not a `res.json()`
      // parse error on a 200 that is not JSON — the ordinary case being a
      // reverse proxy or SPA fallback answering this route with an HTML shell.
      // Without this catch the rejection escaped, `setStatus` was never called,
      // and the page sat on "Checking…" forever: no error, no retry, and not
      // even the ask-an-admin fallback that exists for exactly this moment.
      // Everyone who reaches this page is already locked out, so a dead end
      // here is the worst possible failure.
      let enabled: boolean;
      try {
        enabled = (await getPasswordResetStatus()).email_reset_enabled;
      } catch {
        enabled = false;
      }
      if (cancelled) return;
      setStatus(enabled ? "enabled" : "disabled");
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  async function onSubmit(e: FormEvent): Promise<void> {
    e.preventDefault();
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      const challenge = await getAltchaChallenge();
      const altcha = await solveAltchaChallenge(challenge);
      await requestPasswordReset(email.trim(), altcha);
      setSubmitted(true);
    } catch (err) {
      if (err instanceof PasswordResetDisabledError) {
        setStatus("disabled");
      } else {
        setError(err instanceof Error ? err.message : "Could not send the reset email");
      }
    } finally {
      setBusy(false);
    }
  }

  if (status === "loading") {
    return (
      <main className="lumogis-auth-recovery" role="status" aria-live="polite">
        Checking…
      </main>
    );
  }

  if (submitted) {
    return (
      <main className="lumogis-auth-recovery" data-testid="forgot-password-sent">
        <h1 style={{ margin: 0 }}>Check your email</h1>
        {/* Deliberately says nothing about whether an account exists — the
            server answers identically either way, and undoing that here would
            put the enumeration oracle back in the browser. */}
        <p className="lumogis-auth-recovery__hint">
          If that address has a Lumogis account, a reset link is on its way. The link
          expires shortly, and requesting a new one replaces any earlier link.
        </p>
        <p className="lumogis-auth-recovery__hint">
          <a href="/">Back to sign in</a>
        </p>
      </main>
    );
  }

  if (status === "disabled") {
    return (
      <main className="lumogis-auth-recovery" data-testid="forgot-password-disabled">
        <h1 style={{ margin: 0 }}>Reset your password</h1>
        <p className="lumogis-auth-recovery__hint">
          This Lumogis is not set up to send password-reset emails, so recovery goes
          through your household admin.
        </p>
        <p className="lumogis-auth-recovery__hint">
          <strong>If you are a household member:</strong> ask an admin to reset your
          password from <em>Admin → Users</em>.
        </p>
        <p className="lumogis-auth-recovery__hint">
          <strong>If you are the admin and locked out:</strong> you can reset any
          password from a shell on the Lumogis host:
        </p>
        <p className="lumogis-auth-recovery__hint">
          <code>cd orchestrator &amp;&amp; python -m scripts.reset_password</code>
        </p>
        <p className="lumogis-auth-recovery__hint">
          An admin can enable email reset at <em>Admin → System mail</em>.
        </p>
        <p className="lumogis-auth-recovery__hint">
          <a href="/">Back to sign in</a>
        </p>
      </main>
    );
  }

  return (
    <form
      className="lumogis-auth-recovery"
      onSubmit={onSubmit}
      aria-labelledby="lumogis-forgot-title"
      data-testid="forgot-password-form"
    >
      <h1 id="lumogis-forgot-title" style={{ margin: 0 }}>
        Reset your password
      </h1>
      <p className="lumogis-auth-recovery__hint">
        Enter your account email and we will send you a link to set a new password.
      </p>
      <label htmlFor="lumogis-forgot-email">
        Email
        <input
          id="lumogis-forgot-email"
          type="email"
          autoComplete="username"
          required
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          disabled={busy}
        />
      </label>
      {error !== null && (
        <p role="alert" className="lumogis-auth-recovery__error">
          {error}
        </p>
      )}
      <button type="submit" disabled={busy}>
        {busy ? "Working…" : "Send reset link"}
      </button>
      <p className="lumogis-auth-recovery__hint">
        {/* The proof of work runs in this tab and takes a moment on a phone;
            saying so prevents the "is it stuck?" second click. */}
        Submitting runs a short verification check in your browser.
      </p>
      <p className="lumogis-auth-recovery__hint">
        <a href="/">Back to sign in</a>
      </p>
    </form>
  );
}
