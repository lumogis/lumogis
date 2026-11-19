// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-65. Mounted outside <RequireAuth>, alongside /invite — the visitor
// arriving here is by definition not signed in.

import { useEffect, useState, type FormEvent, type JSX } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";

import { redeemPasswordReset } from "../../api/passwordReset";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { AUTH_LINK_CLASS, AuthCard } from "./AuthCard";

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
      <AuthCard
        title="Reset link is invalid or expired"
        mainProps={{ "data-testid": "reset-password-no-token" }}
        description={
          <>
            Request a new link from{" "}
            <a href="/forgot-password" className={AUTH_LINK_CLASS}>
              Forgot password
            </a>
            .
          </>
        }
      />
    );
  }

  return (
    <AuthCard title="Choose a new password" titleId="lumogis-reset-title">
      <form
        className="flex flex-col gap-4"
        onSubmit={onSubmit}
        aria-labelledby="lumogis-reset-title"
        data-testid="reset-password-form"
      >
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="lumogis-reset-password">New password</Label>
          <Input
            id="lumogis-reset-password"
            type="password"
            autoComplete="new-password"
            required
            minLength={MIN_PASSWORD_LENGTH}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            disabled={busy}
          />
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="lumogis-reset-confirm">Confirm new password</Label>
          <Input
            id="lumogis-reset-confirm"
            type="password"
            autoComplete="new-password"
            required
            minLength={MIN_PASSWORD_LENGTH}
            value={confirm}
            onChange={(e) => setConfirm(e.target.value)}
            disabled={busy}
          />
        </div>
        <p className="m-0 text-sm text-muted-foreground">
          At least {MIN_PASSWORD_LENGTH} characters. Setting a new password signs out
          every device currently using this account.
        </p>
        {error !== null && (
          <Alert variant="destructive">
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}
        <Button type="submit" disabled={busy}>
          {busy ? "Saving…" : "Set new password"}
        </Button>
      </form>
    </AuthCard>
  );
}
