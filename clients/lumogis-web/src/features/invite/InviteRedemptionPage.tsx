// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis

import { useEffect, useState, type FormEvent, type JSX } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";

import {
  INVITE_ONBOARDING_STORAGE_KEY,
  peekInvite,
  redeemInvite,
  type InviteOnboardingHintStored,
} from "../../api/invites";
import { useAuth } from "../../auth/AuthProvider";
import { AuthCard } from "../auth/AuthCard";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

export function InviteRedemptionPage(): JSX.Element {
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const { adoptSession } = useAuth();

  const initialToken = searchParams.get("token") ?? "";
  const [token] = useState(initialToken);
  const [allowsShared, setAllowsShared] = useState<boolean | null>(null);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [peekDone, setPeekDone] = useState(false);

  useEffect(() => {
    const meta = document.createElement("meta");
    meta.name = "referrer";
    meta.content = "no-referrer";
    document.head.appendChild(meta);
    return () => {
      document.head.removeChild(meta);
    };
  }, []);

  useEffect(() => {
    if (!token) {
      setError("Invite link is invalid or expired");
      setPeekDone(true);
      return;
    }
    let cancelled = false;
    void (async () => {
      try {
        const peek = await peekInvite(token);
        if (cancelled) return;
        setAllowsShared(peek.allows_shared);
        setPeekDone(true);
        window.history.replaceState({}, "", "/invite");
      } catch (e) {
        if (cancelled) return;
        setError(e instanceof Error ? e.message : "Invite link is invalid or expired");
        setPeekDone(true);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [token]);

  async function onSubmit(e: FormEvent): Promise<void> {
    e.preventDefault();
    if (busy || !token) return;
    setBusy(true);
    setError(null);
    try {
      const body = await redeemInvite(token, email.trim(), password);
      adoptSession(body);
      const hint: InviteOnboardingHintStored = {
        allows_shared: body.invite_onboarding.allows_shared,
      };
      sessionStorage.setItem(INVITE_ONBOARDING_STORAGE_KEY, JSON.stringify(hint));
      navigate("/chat");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Redemption failed");
    } finally {
      setBusy(false);
    }
  }

  if (!peekDone) {
    return (
      <main className="flex min-h-svh items-center justify-center p-4" role="status">
        Checking invite…
      </main>
    );
  }

  if (error !== null && allowsShared === null) {
    return (
      <AuthCard title="Join household">
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      </AuthCard>
    );
  }

  return (
    <AuthCard
      title="Join household"
      description={
        allowsShared
          ? "Create your account to join this Lumogis household. You will see shared household knowledge alongside your personal space."
          : "Create your account to join this Lumogis household. Your personal memories stay private to you."
      }
    >
      <form className="flex flex-col gap-4" onSubmit={(e) => void onSubmit(e)}>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="invite-email">Email</Label>
          <Input
            id="invite-email"
            type="email"
            autoComplete="username"
            required
            value={email}
            onChange={(ev) => setEmail(ev.target.value)}
            disabled={busy}
          />
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="invite-password">Password (min 12 characters)</Label>
          <Input
            id="invite-password"
            type="password"
            autoComplete="new-password"
            required
            minLength={12}
            value={password}
            onChange={(ev) => setPassword(ev.target.value)}
            disabled={busy}
          />
        </div>
        {error !== null && (
          <Alert variant="destructive">
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}
        <Button type="submit" disabled={busy}>
          {busy ? "Creating account…" : "Create account"}
        </Button>
      </form>
    </AuthCard>
  );
}
