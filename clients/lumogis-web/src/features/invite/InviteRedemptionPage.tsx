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
      <main style={{ padding: "2rem", textAlign: "center" }} role="status">
        Checking invite…
      </main>
    );
  }

  if (error !== null && allowsShared === null) {
    return (
      <main style={{ padding: "2rem", maxWidth: "28rem", margin: "0 auto" }}>
        <h1 style={{ marginTop: 0 }}>Join household</h1>
        <p role="alert">{error}</p>
      </main>
    );
  }

  return (
    <main className="lumogis-login" style={{ maxWidth: "28rem", margin: "2rem auto" }}>
      <h1 style={{ marginTop: 0 }}>Join household</h1>
      <p style={{ opacity: 0.9 }}>
        {allowsShared
          ? "Create your account to join this Lumogis household. You will see shared household knowledge alongside your personal space."
          : "Create your account to join this Lumogis household. Your personal memories stay private to you."}
      </p>
      <form onSubmit={(e) => void onSubmit(e)}>
        <label htmlFor="invite-email">
          Email
          <input
            id="invite-email"
            type="email"
            autoComplete="username"
            required
            value={email}
            onChange={(ev) => setEmail(ev.target.value)}
            disabled={busy}
          />
        </label>
        <label htmlFor="invite-password">
          Password (min 12 characters)
          <input
            id="invite-password"
            type="password"
            autoComplete="new-password"
            required
            minLength={12}
            value={password}
            onChange={(ev) => setPassword(ev.target.value)}
            disabled={busy}
          />
        </label>
        {error !== null && (
          <p role="alert" className="lumogis-login__error">
            {error}
          </p>
        )}
        <button type="submit" disabled={busy}>
          {busy ? "Creating account…" : "Create account"}
        </button>
      </form>
    </main>
  );
}
