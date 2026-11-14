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
// LUM-853 — single-user mode: when the boot refresh fails, the provider asks Core once,
// without a credential, who it serves (`probeSingleUser`). Core with sign-in off answers with
// its single admin; Core with sign-in on answers 401. The mode is the server's answer on this
// load and is never stored, so turning sign-in on takes effect at the next load, and in an
// open tab at its next 401 (`onAuthExpired`). It is UI only: the server authorises every
// request. A stale `lumogis_refresh` cookie is inert with sign-in off (refresh answers 503
// before reading it).
// Known edge (LUM-853 review, P2): sign-in turned OFF under a tab that holds a live session
// member's token — the next /auth/me focus refetch answers as `default`, the tab stays on gate
// "probing" (singleUser false), so it shows no banner and the member-mode controls until a
// reload re-derives the mode. The server authorises nothing differently; a reload fixes it.

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
import { PersistQueryClientProvider } from "@tanstack/react-query-persist-client";
import {
  isCrossTabSignOut,
  pingSignedOut,
  reconcileSignedInMember,
  sessionScopedQueryClient,
  teardownPushForLogout,
  wipeMemberClientState,
} from "./clientStateBoundary";

import { ApiClient, ApiError } from "../api/client";
import { AUTH_ERROR_LITERALS, type LoginResponse, type UserPublic } from "../api/auth";
import { AccessTokenStore } from "../api/tokens";
import { Button } from "../components/Button";
import { TURN_ON_SIGN_IN_DOC_URL } from "../components/SingleUserBanner";
import {
  createQueryPersistenceOptions,
  isQueryPersistenceRuntimeEnabled,
} from "../pwa/queryPersistence";

export type AuthStatus = "loading" | "anonymous" | "authenticated";

export interface AuthValue {
  status: AuthStatus;
  user: UserPublic | null;
  /** true only while the provider runs in single-user mode and `status` is `authenticated`; never persisted. */
  singleUser: boolean;
  client: ApiClient;
  tokens: AccessTokenStore;
  login(email: string, password: string): Promise<LoginAttemptResult>;
  adoptSession(response: LoginResponse): void;
  logout(): Promise<void>;
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

type Gate = "booting" | "no_session" | "probing" | "single_user";

/** D16: the server half of logout is bounded so a hung Core cannot leave "Sign out" dead. */
const LOGOUT_POST_TIMEOUT_MS = 3000;

/** LUM-853: a well-formed single-user identity, as Core's /auth/me returns it with sign-in off. */
export function isSingleUserIdentity(body: unknown): body is UserPublic {
  if (typeof body !== "object" || body === null) return false;
  const u = body as Record<string, unknown>;
  return (
    typeof u.id === "string" &&
    u.id.length > 0 &&
    typeof u.email === "string" &&
    // Core with sign-in off always answers `admin`; a `user` answer cannot come from that branch.
    u.role === "admin"
  );
}

/** Same idea as LOGOUT_POST_TIMEOUT_MS: a hung Core must not leave the splash up forever. */
const SINGLE_USER_PROBE_TIMEOUT_MS = 5000;

/**
 * LUM-853: one unauthenticated GET /api/v1/auth/me, without the refresh-and-retry of
 * ApiClient.fetch. Returns the identity only for a 200 with a well-formed admin; null for
 * anything else (401, any other status, network error, timeout, malformed body), including
 * when a token is stored (then this is not an unauthenticated ask).
 */
async function probeSingleUser(client: ApiClient, tokens: AccessTokenStore): Promise<UserPublic | null> {
  // Unreachable by construction (the failed refresh cleared the store); defensive.
  if (tokens.get() !== null) return null;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), SINGLE_USER_PROBE_TIMEOUT_MS);
  let res: Response;
  try {
    res = await client.fetchOnce("/api/v1/auth/me", { method: "GET", signal: controller.signal });
  } catch {
    return null; // network error or timeout
  } finally {
    clearTimeout(timer);
  }
  if (res.status !== 200) return null;
  let body: unknown;
  try {
    body = await res.json();
  } catch {
    return null;
  }
  return isSingleUserIdentity(body) ? body : null;
}

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
  const gateRef = useRef<Gate>(gate);
  gateRef.current = gate;

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
      const single = await probeSingleUser(client, tokens);
      if (cancelled) return;
      if (single) {
        // Seed before the gate flips so meQuery starts with data (no second /auth/me at boot).
        queryClient.setQueryData(ME_KEY, single);
        setGate("single_user");
      } else {
        setGate("no_session");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [client, tokens, queryClient, skipRefreshOnMount]);

  const meQuery = useQuery({
    queryKey: ME_KEY,
    queryFn: () => client.getJson<UserPublic>("/api/v1/auth/me"),
    enabled: gate === "probing" || gate === "single_user",
    staleTime: 30_000,
    refetchOnWindowFocus: "always",
  });

  const meId = meQuery.isSuccess ? (meQuery.data?.id ?? null) : null;
  meIdRef.current = meId;

  // Sign-in reconcile: covers login, adoptSession and boot-refresh success, because all
  // three resolve through ['auth','me']. Children do not render until it has finished.
  useEffect(() => {
    if ((gate !== "probing" && gate !== "single_user") || meId === null || meId === reconciledUserId) return;
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
    if (gate === "booting") return "loading";
    if (gate === "no_session") return "anonymous";
    if (meQuery.isPending) return "loading";
    if (meQuery.isError) {
      const e = meQuery.error;
      if (e instanceof ApiError && e.status === 401) return "anonymous";
      // LUM-853: in single-user mode a slow or restarting Core (non-401) keeps the last good
      // identity; the alternative is a sign-in form that cannot sign in. A 401 never lands
      // here: ApiClient.fetch already ran onAuthExpired, which set the gate to no_session.
      if (gate === "single_user" && meQuery.data && reconciledUserId === meQuery.data.id) {
        return "authenticated";
      }
      return "anonymous";
    }
    if (meQuery.data) return reconciledUserId === meQuery.data.id ? "authenticated" : "loading";
    return "anonymous";
  }, [gate, meQuery.isPending, meQuery.isError, meQuery.error, meQuery.data, reconciledUserId]);

  // Exposed only once reconciled, so nothing sees a member before the previous one's
  // state is gone.
  const user = status === "authenticated" && meQuery.data ? meQuery.data : null;
  const singleUser = gate === "single_user" && status === "authenticated";

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
    // Single-user mode has no session to end: a sign-out would show a sign-in form that
    // cannot sign in. Turning sign-in on is the way out (banner). `beginSignOut` is not
    // guarded: it is the path `onAuthExpired` takes when sign-in is turned on under an open tab.
    if (gateRef.current === "single_user") return Promise.resolve();
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

  const value = useMemo<AuthValue>(
    () => ({ status, user, singleUser, client, tokens, login, adoptSession, logout }),
    [status, user, singleUser, client, tokens, login, adoptSession, logout],
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
  const { status, login } = useAuth();
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

interface LoginFormProps {
  onSubmit(email: string, password: string): Promise<LoginAttemptResult>;
}

function LoginForm({ onSubmit }: LoginFormProps): JSX.Element {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [flash, setFlash] = useState<string | null>(null);
  // LUM-853: set when Core answered the sign-in with "sign-in is off" (for example Core was
  // still starting when the page loaded, so the boot probe could not see it).
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

  return (
    <form
      className="lumogis-login"
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
      <h1 id="lumogis-login-title" style={{ margin: 0 }}>
        Sign in
      </h1>
      {flash !== null && (
        <p role="status" className="lumogis-login__flash">
          {flash}
        </p>
      )}
      <label htmlFor="lumogis-login-email">
        Email
        <input
          id="lumogis-login-email"
          type="email"
          autoComplete="username"
          required
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          disabled={busy}
        />
      </label>
      <label htmlFor="lumogis-login-password">
        Password
        <input
          id="lumogis-login-password"
          type="password"
          autoComplete="current-password"
          required
          minLength={12}
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          disabled={busy}
        />
      </label>
      {error !== null && (
        <p role="alert" className="lumogis-login__error">
          {error}
        </p>
      )}
      {signInOff && (
        <p className="lumogis-login__recovery">
          <a href={TURN_ON_SIGN_IN_DOC_URL} target="_blank" rel="noopener noreferrer">
            How to turn on sign-in
          </a>
        </p>
      )}
      <Button type="submit" variant="primary" disabled={busy}>
        {busy ? "Signing in…" : "Sign in"}
      </Button>
      {/* LUM-65 Pass 1: the login screen used to offer no recovery path at all.
          The link always renders — the page itself decides whether it can offer
          an email reset or only the ask-an-admin / operator-CLI guidance. */}
      <p className="lumogis-login__recovery">
        <a href="/forgot-password">Forgot password?</a>
      </p>
    </form>
  );
}

/** LUM-853: display copy only; no mode decision reads the 503 detail. */
export const SIGN_IN_OFF_LOGIN_COPY =
  "Sign-in is turned off on this Lumogis, so there is no account to sign in to. Reload this page to open Lumogis.";

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
