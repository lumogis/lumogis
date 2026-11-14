// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-65. Mounted outside <RequireAuth>, alongside /invite — the visitor
// arriving here is by definition not signed in.

import { useEffect, useState, type FormEvent, type JSX } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";

import { redeemPasswordReset } from "../../api/passwordReset";

const MIN_PASSWORD_LENGTH = 12;

export function ResetPasswordPage(): JSX.Element {
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();

  const [token] = useState(searchParams.get("token") ?? "");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    // Same treatment the invite page gives its token: strip it from the URL so
    // it does not sit in history, and suppress the referrer so a link on the
    // page cannot carry a live reset token to a third-party host.
    const meta = document.createElement("meta");
    meta.name = "referrer";
    meta.content = "no-referrer";
    document.head.appendChild(meta);
    if (searchParams.get("token")) {
      window.history.replaceState({}, "", "/reset-password");
    }
    return () => {
      document.head.removeChild(meta);
    };
    // Intentionally once, on mount: `token` is captured in state above.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function onSubmit(e: FormEvent): Promise<void> {
    e.preventDefault();
    if (busy) return;
    if (password !== confirm) {
      setError("The two passwords do not match.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await redeemPasswordReset(token, password);
      // Every existing session for this account is already dead server-side
      // (the redeem bumps token_version and revokes sessions), so the only
      // sensible next step is a fresh sign-in.
      try {
        sessionStorage.setItem(
          "lumogis_login_flash",
          "Password updated. Sign in with your new password.",
        );
      } catch {
        /* non-fatal */
      }
      navigate("/");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Reset link is invalid or expired");
    } finally {
      setBusy(false);
    }
  }

  if (!token) {
    return (
      <main className="lumogis-auth-recovery" data-testid="reset-password-no-token">
        <h1 style={{ margin: 0 }}>Reset link is invalid or expired</h1>
        <p className="lumogis-auth-recovery__hint">
          Request a new link from <a href="/forgot-password">Forgot password</a>.
        </p>
      </main>
    );
  }

  return (
    <form
      className="lumogis-auth-recovery"
      onSubmit={onSubmit}
      aria-labelledby="lumogis-reset-title"
      data-testid="reset-password-form"
    >
      <h1 id="lumogis-reset-title" style={{ margin: 0 }}>
        Choose a new password
      </h1>
      <label htmlFor="lumogis-reset-password">
        New password
        <input
          id="lumogis-reset-password"
          type="password"
          autoComplete="new-password"
          required
          minLength={MIN_PASSWORD_LENGTH}
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          disabled={busy}
        />
      </label>
      <label htmlFor="lumogis-reset-confirm">
        Confirm new password
        <input
          id="lumogis-reset-confirm"
          type="password"
          autoComplete="new-password"
          required
          minLength={MIN_PASSWORD_LENGTH}
          value={confirm}
          onChange={(e) => setConfirm(e.target.value)}
          disabled={busy}
        />
      </label>
      <p className="lumogis-auth-recovery__hint">
        At least {MIN_PASSWORD_LENGTH} characters. Setting a new password signs out
        every device currently using this account.
      </p>
      {error !== null && (
        <p role="alert" className="lumogis-auth-recovery__error">
          {error}
        </p>
      )}
      <button type="submit" disabled={busy}>
        {busy ? "Saving…" : "Set new password"}
      </button>
    </form>
  );
}
