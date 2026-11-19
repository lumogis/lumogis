// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-981 — "Create the admin account". Mounted outside <RequireAuth> at /setup. The first
// person to open a new Lumogis creates the household admin here; Core allows it only once.

import { useState, type FormEvent, type JSX } from "react";
import { Navigate, useNavigate } from "react-router-dom";

import { SetupClaimError, claimSetup } from "../../api/setup";
import { useAuth } from "../../auth/AuthProvider";
import { SIGN_IN_DOC_URL } from "../../auth/docLinks";
import { AUTH_LINK_CLASS, AuthCard } from "../auth/AuthCard";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

const MIN_PASSWORD_LENGTH = 12;

const HOST_NOT_ALLOWED_COPY =
  "Open Lumogis at this computer's network address (for example http://192.168.1.20) to create the admin account. If you reach Lumogis through a public name, set LUMOGIS_PUBLIC_ORIGIN in .env first.";

interface Failure {
  message: string;
  /** Setup is over (409) or the outcome is unknown (500): offer "Go to sign in". */
  offerSignIn: boolean;
  alreadyCreated: boolean;
}

function failureFor(err: unknown): Failure {
  if (err instanceof SetupClaimError) {
    if (err.status === 409) {
      return {
        message: "The admin account on this Lumogis has already been created.",
        offerSignIn: true,
        alreadyCreated: true,
      };
    }
    if (
      err.status === 403 &&
      (err.detail === "setup_host_not_allowed" || err.detail === "origin mismatch")
    ) {
      return { message: HOST_NOT_ALLOWED_COPY, offerSignIn: false, alreadyCreated: false };
    }
    if (err.status === 422) {
      return {
        message:
          err.detail ?? "Check the email address and use a password of at least 12 characters.",
        offerSignIn: false,
        alreadyCreated: false,
      };
    }
    if (err.status === 429) {
      return {
        message: "Too many attempts. Try again in a minute.",
        offerSignIn: false,
        alreadyCreated: false,
      };
    }
    if (err.status === 503) {
      // database_unavailable: the claim rolled back, so no account exists yet.
      // Offering "Go to sign in" here would lead to a sign-in form with no account.
      return {
        message: "Lumogis could not reach its database. Try again.",
        offerSignIn: false,
        alreadyCreated: false,
      };
    }
  }
  return {
    message: "Lumogis could not create the account. Try again.",
    offerSignIn: true,
    alreadyCreated: false,
  };
}

export function SetupPage(): JSX.Element {
  const { needsSetup, status, adoptSession, leaveSetup } = useAuth();
  const navigate = useNavigate();

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  // True from the moment the claim succeeded. After adoptSession `needsSetup` turns false in
  // the same render pass as navigate("/chat"); without this the "not needed" redirect to "/"
  // could win.
  const [done, setDone] = useState(false);
  const [failure, setFailure] = useState<Failure | null>(null);
  const [mismatch, setMismatch] = useState(false);

  if (status === "loading" && !done) {
    return (
      <main role="status" aria-live="polite" className="flex min-h-svh items-center justify-center p-4">
        Loading…
      </main>
    );
  }
  if (!needsSetup && !busy && !done) return <Navigate to="/" replace />;

  function goToSignIn(): void {
    leaveSetup();
    navigate("/");
  }

  async function onSubmit(e: FormEvent): Promise<void> {
    e.preventDefault();
    if (busy) return;
    setFailure(null);
    if (password !== confirm) {
      setMismatch(true);
      return;
    }
    setMismatch(false);
    setBusy(true);
    try {
      const response = await claimSetup(email.trim(), password);
      setDone(true);
      adoptSession(response);
      navigate("/chat");
    } catch (err) {
      setFailure(failureFor(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <AuthCard
      title="Create the admin account"
      titleId="lumogis-setup-title"
      description="This is the first account on this Lumogis. It becomes the household admin, and you can invite everyone else afterwards. It can only be created once."
    >
      <form
        className="flex flex-col gap-4"
        onSubmit={(e) => void onSubmit(e)}
        aria-labelledby="lumogis-setup-title"
        data-testid="setup-form"
      >
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="lumogis-setup-email">Email</Label>
          <Input
            id="lumogis-setup-email"
            type="email"
            autoComplete="username"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            disabled={busy}
          />
          <p className="m-0 text-xs text-muted-foreground">
            Use an ordinary email address, for example name@example.com. Addresses ending in .local
            or .test are not accepted.
          </p>
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="lumogis-setup-password">Password</Label>
          <Input
            id="lumogis-setup-password"
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
          <Label htmlFor="lumogis-setup-confirm">Confirm password</Label>
          <Input
            id="lumogis-setup-confirm"
            type="password"
            autoComplete="new-password"
            required
            minLength={MIN_PASSWORD_LENGTH}
            value={confirm}
            onChange={(e) => setConfirm(e.target.value)}
            disabled={busy}
          />
          <p className="m-0 text-xs text-muted-foreground">At least {MIN_PASSWORD_LENGTH} characters.</p>
        </div>
        {mismatch && (
          <Alert variant="destructive">
            <AlertDescription>The two passwords do not match.</AlertDescription>
          </Alert>
        )}
        {failure !== null && (
          <Alert variant="destructive">
            <AlertDescription>{failure.message}</AlertDescription>
          </Alert>
        )}
        {failure?.offerSignIn && (
          <Button type="button" variant="default" onClick={goToSignIn}>
            Go to sign in
          </Button>
        )}
        {failure?.alreadyCreated && (
          <p className="m-0 text-sm text-muted-foreground">
            If you didn&apos;t create it, someone else on your network did. Remove this install and
            start again (see the{" "}
            <a href={SIGN_IN_DOC_URL} target="_blank" rel="noopener noreferrer" className={AUTH_LINK_CLASS}>
              quickstart
            </a>
            ).
          </p>
        )}
        <Button type="submit" disabled={busy}>
          {busy ? "Creating…" : "Create account"}
        </Button>
      </form>
    </AuthCard>
  );
}
