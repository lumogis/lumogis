// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// AuthProvider — parent plan §"Phase 1 Pass 1.1 item 4" + `lumogis_web_admin_shell`
// §useUser refetch (TanStack Query).
//
// We intentionally co-locate the AuthContext provider, the `useAuth` /
// `useUser` hooks, and the `humaniseLoginError` helper in this file. The
// react-refresh `only-export-components` rule degrades HMR slightly when
// components and constants share a module — this is a DX trade-off, not a
// correctness one, so we silence the rule for this file.
/* eslint-disable react-refresh/only-export-components */
//
// `GET /api/v1/auth/me` is loaded via TanStack Query (`['auth','me']`) with
// `staleTime: 30_000` and `refetchOnWindowFocus: 'always'` (admin shell plan).
//
// Per parent plan §Security decisions: the access token NEVER touches
// localStorage. It lives in `tokens` (in-memory only) for the tab's
// lifetime; the HttpOnly `lumogis_refresh` cookie is the durable credential.
//
// LUM-886: `./clientStateBoundary` is the only place that decides what browser state is
// removed when a member signs out or a different member signs in on the same device.
// This file only calls it: a sign-in reconcile before children render (`status` stays
// "loading" until it finishes), the sign-out wipe after the gate flips, the push teardown
// at explicit logout, and a cross-tab `storage` listener. Every new per-member client
// store must register in the boundary module (contract for LUM-205 / LUM-207).
//
// LUM-897: `children` do not get the raw QueryClient. They get a session handle
// (`sessionScopedQueryClient`), renewed after every wipe, so a slow mutation callback of the
// previous member writes into nothing instead of the next member's cache. This provider
// itself keeps the raw client (`['auth','me']`, the wipes, persistence).
//
// LUM-981 — first-admin setup: when the boot refresh fails, the provider asks Core once,
// without a credential, whether the install still waits for its first admin
// (`GET /api/v1/setup/status`). `needed` → gate `needs_setup` (RequireAuth sends the visitor to
// /setup); `not_needed` → the sign-in form; `unreachable` (Core still starting, a 5xx, a timeout)
// → gate `starting`, a splash that asks again every 5 s and never shows the sign-in form or the
// setup page on a guess. Lumogis Web needs sign-in on: with it off Core answers the
// sign-in 503 and the form says so (SIGN_IN_OFF_LOGIN_COPY). The setup answer is the server's
// on this load and is never stored.

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { QueryClient, QueryClientProvider, useQuery, useQueryClient } from "@tanstack/react-query";
import { Navigate } from "react-router-dom";
import { PersistQueryClientProvider } from "@tanstack/react-query-persist-client";
import {
  isCrossTabSignOut,
  pingSignedOut,
  reconcileSignedInMember,
  sessionScopedQueryClient,
  teardownPushForLogout,
  wipeMemberClientState,
} from "./clientStateBoundary";

import { ApiClient } from "../api/client";
import { AUTH_ERROR_LITERALS, type LoginResponse, type UserPublic } from "../api/auth";
import { getSetupStatus } from "../api/setup";
import { AccessTokenStore } from "../api/tokens";
import { Alert, AlertDescription } from "../components/ui/alert";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { Label } from "../components/ui/label";
import { AUTH_LINK_CLASS, AuthCard } from "../features/auth/AuthCard";
import { SIGN_IN_DOC_URL } from "./docLinks";
import {
  createQueryPersistenceOptions,
  isQueryPersistenceRuntimeEnabled,
} from "../pwa/queryPersistence";

export type AuthStatus = "loading" | "anonymous" | "authenticated";

export interface AuthValue {
  status: AuthStatus;
  user: UserPublic | null;
  /** The install still waits for its first admin (boot asked Core; never persisted). */
  needsSetup: boolean;
  /** Core did not answer the setup question properly (still starting); the provider re-asks. */
  starting: boolean;
  client: ApiClient;
  tokens: AccessTokenStore;
  login(email: string, password: string): Promise<LoginAttemptResult>;
  adoptSession(response: LoginResponse): void;
  logout(): Promise<void>;
  /** The setup page calls this on a 409: setup is over, show the sign-in form. */
  leaveSetup(): void;
}

export type LoginAttemptResult =
  | { ok: true; user: UserPublic }
  | { ok: false; status: number; detail: string };

const AuthContext = createContext<AuthValue | null>(null);

const ME_KEY = ["auth", "me"] as const;

export interface AuthProviderProps {
  children: ReactNode;
  client?: ApiClient;
  tokens?: AccessTokenStore;
  skipRefreshOnMount?: boolean;
}

export function AuthProvider({
  children,
  client: providedClient,
  tokens: providedTokens,
  skipRefreshOnMount = false,
}: AuthProviderProps): JSX.Element {
  const queryClient = useMemo(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: { retry: false },
        },
      }),
    [],
  );

  const persistOptions = useMemo(
    () => (isQueryPersistenceRuntimeEnabled() ? createQueryPersistenceOptions() : null),
    [],
  );

  const inner = (
    <AuthProviderCore client={providedClient} tokens={providedTokens} skipRefreshOnMount={skipRefreshOnMount}>
      {children}
    </AuthProviderCore>
  );

  if (persistOptions) {
    return (
      <PersistQueryClientProvider client={queryClient} persistOptions={persistOptions}>
        {inner}
      </PersistQueryClientProvider>
    );
  }

  return <QueryClientProvider client={queryClient}>{inner}</QueryClientProvider>;
}

type Gate = "booting" | "no_session" | "needs_setup" | "starting" | "probing";

/** D16: the server half of logout is bounded so a hung Core cannot leave "Sign out" dead. */
const LOGOUT_POST_TIMEOUT_MS = 3000;

/** While `starting`, how often Core is asked again. */
const STARTING_POLL_MS = 5000;

function AuthProviderCore({
  children,
  client: providedClient,
  tokens: providedTokens,
  skipRefreshOnMount = false,
}: AuthProviderProps): JSX.Element {
  const tokens = useMemo(() => providedTokens ?? new AccessTokenStore(), [providedTokens]);
  const client = useMemo(
    () => providedClient ?? new ApiClient({ tokens }),
    [providedClient, tokens],
  );
  const queryClient = useQueryClient();

  const [gate, setGate] = useState<Gate>(() => (skipRefreshOnMount ? "probing" : "booting"));

  const [reconciledUserId, setReconciledUserId] = useState<string | null>(null);
  const [signOutSeq, setSignOutSeq] = useState(0);

  const reconciledUserIdRef = useRef<string | null>(null);
  const previousUserInTabRef = useRef<string | null>(null);
  const reconcileRunRef = useRef(0);
  const meIdRef = useRef<string | null>(null);
  const signOutWaitersRef = useRef<Array<() => void>>([]);
  const pendingSignOutRef = useRef<Promise<void> | null>(null);
  const logoutInFlightRef = useRef<Promise<void> | null>(null);

  // LUM-897: the client the children see. A new handle after each wipe (the reconcile runs
  // its wipe before markReconciled; the sign-out wipe renews it when it finishes).
  const [sessionClient, setSessionClient] = useState(() => sessionScopedQueryClient(queryClient));
  const renewSessionClient = useCallback(() => {
    setSessionClient(sessionScopedQueryClient(queryClient));
  }, [queryClient]);

  const markReconciled = useCallback(
    (id: string | null) => {
      reconciledUserIdRef.current = id;
      setReconciledUserId(id);
      if (id !== null) renewSessionClient();
    },
    [renewSessionClient],
  );

  /**
   * Local sign-out: flip the gate, drop the token and session identity, then let the
   * post-commit effect below run the wipe (D8). Idempotent; the returned promise resolves
   * once the wipe has finished.
   */
  const beginSignOut = useCallback((): Promise<void> => {
    client.setSessionUserId(null);
    tokens.clear();
    markReconciled(null);
    setGate("no_session");
    const p = new Promise<void>((resolve) => {
      signOutWaitersRef.current.push(resolve);
    });
    pendingSignOutRef.current = p;
    void p.then(() => {
      if (pendingSignOutRef.current === p) pendingSignOutRef.current = null;
    });
    setSignOutSeq((n) => n + 1);
    return p;
  }, [client, tokens, markReconciled]);

  const beginSignOutRef = useRef(beginSignOut);
  beginSignOutRef.current = beginSignOut;

  useEffect(() => {
    client.setAuthExpiredHandler(() => {
      void beginSignOutRef.current();
    });
  }, [client]);

  // Sign-out wipe (D8): runs after the commit that unmounted the signed-in pages, so
  // their unmount-time draft writes are created before the wipe's IndexedDB transactions.
  useEffect(() => {
    if (signOutSeq === 0) return;
    void wipeMemberClientState(queryClient).finally(() => {
      renewSessionClient();
      const waiters = signOutWaitersRef.current.splice(0);
      waiters.forEach((resolve) => resolve());
    });
  }, [signOutSeq, queryClient, renewSessionClient]);

  // Never leave a `logout()` caller hanging if the provider unmounts mid-wipe.
  useEffect(() => {
    const waiters = signOutWaitersRef;
    return () => {
      waiters.current.splice(0).forEach((resolve) => resolve());
    };
  }, []);

  // D13: another tab of this browser signed a different member in, or logged out.
  useEffect(() => {
    const onStorage = (ev: StorageEvent) => {
      if (isCrossTabSignOut(ev, reconciledUserIdRef.current)) void beginSignOutRef.current();
    };
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, []);

  useEffect(() => {
    if (skipRefreshOnMount) return;
    let cancelled = false;
    void (async () => {
      const ok = await client.tryRefresh();
      if (cancelled) return;
      if (ok) {
        setGate("probing");
        return;
      }
      const setup = await getSetupStatus();
      if (cancelled) return;
      setGate(setup === "needed" ? "needs_setup" : setup === "not_needed" ? "no_session" : "starting");
    })();
    return () => {
      cancelled = true;
    };
  }, [client, skipRefreshOnMount]);

  // `starting`: Core did not answer the setup question. Ask again (refresh first, in case a
  // session exists after all) until it answers, then leave.
  useEffect(() => {
    if (gate !== "starting") return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const tick = async (): Promise<void> => {
      const ok = await client.tryRefresh();
      if (cancelled) return;
      if (ok) {
        setGate("probing");
        return;
      }
      const setup = await getSetupStatus();
      if (cancelled) return;
      if (setup === "unreachable") {
        timer = setTimeout(() => void tick(), STARTING_POLL_MS);
        return;
      }
      setGate(setup === "needed" ? "needs_setup" : "no_session");
    };
    timer = setTimeout(() => void tick(), STARTING_POLL_MS);
    return () => {
      cancelled = true;
      if (timer !== undefined) clearTimeout(timer);
    };
  }, [gate, client]);

  const meQuery = useQuery({
    queryKey: ME_KEY,
    queryFn: () => client.getJson<UserPublic>("/api/v1/auth/me"),
    enabled: gate === "probing",
    staleTime: 30_000,
    refetchOnWindowFocus: "always",
  });

  const meId = meQuery.isSuccess ? (meQuery.data?.id ?? null) : null;
  meIdRef.current = meId;

  // Sign-in reconcile: covers login, adoptSession and boot-refresh success, because all
  // three resolve through ['auth','me']. Children do not render until it has finished.
  useEffect(() => {
    if (gate !== "probing" || meId === null || meId === reconciledUserId) return;
    const run = ++reconcileRunRef.current;
    let cancelled = false;
    void (async () => {
      try {
        await reconcileSignedInMember(meId, {
          queryClient,
          previousUserIdInTab: previousUserInTabRef.current,
          keepQueryKey: ME_KEY,
        });
      } catch {
        /* reconcileSignedInMember never throws by contract; never stick on the splash */
      }
      if (cancelled || run !== reconcileRunRef.current || meIdRef.current !== meId) return;
      previousUserInTabRef.current = meId;
      client.setSessionUserId(meId);
      markReconciled(meId);
    })();
    return () => {
      cancelled = true;
    };
  }, [gate, meId, reconciledUserId, queryClient, client, markReconciled]);

  const status: AuthStatus = useMemo(() => {
    if (gate === "booting" || gate === "starting") return "loading";
    if (gate === "needs_setup" || gate === "no_session") return "anonymous";
    if (meQuery.isPending) return "loading";
    if (meQuery.isError) return "anonymous";
    if (meQuery.data) return reconciledUserId === meQuery.data.id ? "authenticated" : "loading";
    return "anonymous";
  }, [gate, meQuery.isPending, meQuery.isError, meQuery.data, reconciledUserId]);

  // Exposed only once reconciled, so nothing sees a member before the previous one's
  // state is gone.
  const user = status === "authenticated" && meQuery.data ? meQuery.data : null;
  const needsSetup = gate === "needs_setup";
  const starting = gate === "starting";

  // D17: a new session never starts while a sign-out wipe is pending, and the previous
  // member's id is never the session id while the new member's token is live (D14).
  const startSession = useCallback(
    (response: LoginResponse) => {
      client.setSessionUserId(null);
      tokens.set(response.access_token);
      queryClient.setQueryData(ME_KEY, response.user);
      setGate("probing");
    },
    [client, tokens, queryClient],
  );

  const login = useCallback<AuthValue["login"]>(
    async (email, password) => {
      const url = "/api/v1/auth/login";
      const res = await fetch(url, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password }),
      });
      if (!res.ok) {
        let detail = "login failed";
        try {
          const body = (await res.json()) as { detail?: unknown };
          if (typeof body.detail === "string") detail = body.detail;
        } catch {
          /* keep default */
        }
        return { ok: false, status: res.status, detail };
      }
      const body = (await res.json()) as LoginResponse;
      if (pendingSignOutRef.current) await pendingSignOutRef.current;
      startSession(body);
      return { ok: true, user: body.user };
    },
    [startSession],
  );

  const adoptSession = useCallback<AuthValue["adoptSession"]>(
    (response) => {
      const pending = pendingSignOutRef.current;
      // Known edge (LUM-886 verify): `InviteRedemptionPage` writes the invite onboarding
      // hint right after this returns; if a sign-out wipe is still pending, its full-mode
      // invite-hint step can remove it, and the invitee skips that onboarding step. Needs
      // a sign-out in flight during redemption; the hint is non-sensitive flow state.
      if (pending) void pending.then(() => startSession(response));
      else startSession(response);
    },
    [startSession],
  );

  const logout = useCallback<AuthValue["logout"]>(() => {
    if (logoutInFlightRef.current) return logoutInFlightRef.current;
    const p = (async () => {
      // Push teardown first: the access token is still in memory, and it never goes
      // through the refresh / onAuthExpired path.
      await teardownPushForLogout(client);
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), LOGOUT_POST_TIMEOUT_MS);
      try {
        await fetch("/api/v1/auth/logout", {
          method: "POST",
          credentials: "include",
          signal: controller.signal,
        });
      } catch {
        /* idempotent; a hung or failed server call must not keep this session alive */
      } finally {
        clearTimeout(timer);
      }
      pingSignedOut();
      await beginSignOut();
    })().finally(() => {
      logoutInFlightRef.current = null;
    });
    logoutInFlightRef.current = p;
    return p;
  }, [client, beginSignOut]);

  const leaveSetup = useCallback<AuthValue["leaveSetup"]>(() => {
    setGate("no_session");
  }, []);

  const value = useMemo<AuthValue>(
    () => ({
      status,
      user,
      needsSetup,
      starting,
      client,
      tokens,
      login,
      adoptSession,
      logout,
      leaveSetup,
    }),
    [status, user, needsSetup, starting, client, tokens, login, adoptSession, logout, leaveSetup],
  );

  return (
    <AuthContext.Provider value={value}>
      <QueryClientProvider client={sessionClient}>{children}</QueryClientProvider>
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthValue {
  const v = useContext(AuthContext);
  if (v === null) throw new Error("useAuth() must be used inside <AuthProvider>");
  return v;
}

export function useUser(): UserPublic | null {
  return useAuth().user;
}

export function RequireAuth({ children }: { children: ReactNode }): JSX.Element {
  const { status, login, needsSetup, starting } = useAuth();
  if (needsSetup) return <Navigate to="/setup" replace />;
  if (starting) return <StartingSplash />;
  if (status === "loading") return <LoadingSplash />;
  if (status === "anonymous") return <LoginForm onSubmit={login} />;
  return <>{children}</>;
}

function LoadingSplash(): JSX.Element {
  return (
    <main role="status" aria-live="polite" style={{ padding: "2rem", textAlign: "center" }}>
      Loading…
    </main>
  );
}

function StartingSplash(): JSX.Element {
  return (
    <main role="status" aria-live="polite" className="p-8 text-center">
      Lumogis is still starting. This page will continue on its own.
    </main>
  );
}

interface LoginFormProps {
  onSubmit(email: string, password: string): Promise<LoginAttemptResult>;
}

function LoginForm({ onSubmit }: LoginFormProps): JSX.Element {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [flash, setFlash] = useState<string | null>(null);
  // Set when Core answered the sign-in with "sign-in is off" (a developer or test setup).
  const [signInOff, setSignInOff] = useState(false);

  useEffect(() => {
    try {
      const m = sessionStorage.getItem("lumogis_login_flash");
      if (m) {
        sessionStorage.removeItem("lumogis_login_flash");
        setFlash(m);
      }
    } catch {
      /* ignore */
    }
  }, []);

  // LUM-993 Pass 8: on the shared sign-in card, like the other signed-out pages.
  // Ids, roles and messages are unchanged (tests and password managers use them).
  return (
    <AuthCard title="Sign in" titleId="lumogis-login-title">
      <form
        className="flex flex-col gap-4"
        aria-labelledby="lumogis-login-title"
        onSubmit={async (e) => {
          e.preventDefault();
          if (busy) return;
          setBusy(true);
          setError(null);
          setSignInOff(false);
          const result = await onSubmit(email, password);
          if (!result.ok) {
            setError(humaniseLoginError(result.status, result.detail));
            setSignInOff(
              result.status === 503 && result.detail === AUTH_ERROR_LITERALS.LOGIN_DISABLED_DEV_MODE,
            );
          }
          setBusy(false);
        }}
      >
        {flash !== null && (
          <p role="status" className="m-0 text-sm text-muted-foreground">
            {flash}
          </p>
        )}
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="lumogis-login-email">Email</Label>
          <Input
            id="lumogis-login-email"
            type="email"
            autoComplete="username"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            disabled={busy}
          />
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="lumogis-login-password">Password</Label>
          <Input
            id="lumogis-login-password"
            type="password"
            autoComplete="current-password"
            required
            minLength={12}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            disabled={busy}
          />
        </div>
        {error !== null && (
          <Alert variant="destructive">
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}
        {signInOff && (
          <p className="m-0 text-sm">
            <a href={SIGN_IN_DOC_URL} target="_blank" rel="noopener noreferrer" className={AUTH_LINK_CLASS}>
              About sign-in
            </a>
          </p>
        )}
        <Button type="submit" disabled={busy}>
          {busy ? "Signing in…" : "Sign in"}
        </Button>
        {/* LUM-65 Pass 1: the login screen used to offer no recovery path at all.
            The link always renders — the page itself decides whether it can offer
            an email reset or only the ask-an-admin / operator-CLI guidance. */}
        <p className="m-0 text-sm">
          <a href="/forgot-password" className={AUTH_LINK_CLASS}>
            Forgot password?
          </a>
        </p>
      </form>
    </AuthCard>
  );
}

/** Display copy only; no mode decision reads the 503 detail. */
export const SIGN_IN_OFF_LOGIN_COPY =
  "Sign-in is turned off on this Lumogis server, and Lumogis Web needs it. Set AUTH_ENABLED=true in .env, then run docker compose up -d.";

export function humaniseLoginError(status: number, detail: string): string {
  if (status === 401 && detail === "invalid credentials") {
    return "Email or password is incorrect.";
  }
  if (status === 429) {
    return "Too many failed attempts. Try again in a minute.";
  }
  if (status === 503 && detail === AUTH_ERROR_LITERALS.LOGIN_DISABLED_DEV_MODE) {
    return SIGN_IN_OFF_LOGIN_COPY;
  }
  return detail || `Sign-in failed (HTTP ${status}).`;
}
