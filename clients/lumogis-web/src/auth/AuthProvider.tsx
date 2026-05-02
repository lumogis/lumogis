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

import { ApiClient, ApiError } from "../api/client";
import type { LoginResponse, UserPublic } from "../api/auth";
import { AccessTokenStore } from "../api/tokens";
import {
  createQueryPersistenceOptions,
  isQueryPersistenceRuntimeEnabled,
} from "../pwa/queryPersistence";

export type AuthStatus = "loading" | "anonymous" | "authenticated";

export interface AuthValue {
  status: AuthStatus;
  user: UserPublic | null;
  client: ApiClient;
  tokens: AccessTokenStore;
  login(email: string, password: string): Promise<LoginAttemptResult>;
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

type Gate = "booting" | "no_session" | "probing";

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

  const setStateRef = useRef({ setGate, queryClient, tokens });
  setStateRef.current = { setGate, queryClient, tokens };

  useEffect(() => {
    client.setAuthExpiredHandler(() => {
      const { setGate: sg, queryClient: qc, tokens: tk } = setStateRef.current;
      tk.clear();
      qc.removeQueries({ queryKey: ME_KEY });
      sg("no_session");
    });
  }, [client]);

  useEffect(() => {
    if (skipRefreshOnMount) return;
    let cancelled = false;
    void (async () => {
      const ok = await client.tryRefresh();
      if (cancelled) return;
      setGate(ok ? "probing" : "no_session");
    })();
    return () => {
      cancelled = true;
    };
  }, [client, skipRefreshOnMount]);

  const meQuery = useQuery({
    queryKey: ME_KEY,
    queryFn: () => client.getJson<UserPublic>("/api/v1/auth/me"),
    enabled: gate === "probing",
    staleTime: 30_000,
    refetchOnWindowFocus: "always",
  });

  const status: AuthStatus = useMemo(() => {
    if (gate === "booting") return "loading";
    if (gate === "no_session") return "anonymous";
    if (meQuery.isPending) return "loading";
    if (meQuery.isError) {
      const e = meQuery.error;
      if (e instanceof ApiError && e.status === 401) return "anonymous";
      return "anonymous";
    }
    if (meQuery.data) return "authenticated";
    return "anonymous";
  }, [gate, meQuery.isPending, meQuery.isError, meQuery.error, meQuery.data]);

  const user = meQuery.isSuccess && meQuery.data ? meQuery.data : null;

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
      tokens.set(body.access_token);
      queryClient.setQueryData(ME_KEY, body.user);
      setGate("probing");
      return { ok: true, user: body.user };
    },
    [tokens, queryClient],
  );

  const logout = useCallback<AuthValue["logout"]>(async () => {
    try {
      await fetch("/api/v1/auth/logout", { method: "POST", credentials: "include" });
    } catch {
      /* idempotent */
    }
    tokens.clear();
    queryClient.removeQueries({ queryKey: ME_KEY });
    setGate("no_session");
  }, [tokens, queryClient]);

  const value = useMemo<AuthValue>(
    () => ({ status, user, client, tokens, login, logout }),
    [status, user, client, tokens, login, logout],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
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
        const result = await onSubmit(email, password);
        if (!result.ok) setError(humaniseLoginError(result.status, result.detail));
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
      <button type="submit" disabled={busy}>
        {busy ? "Signing in…" : "Sign in"}
      </button>
    </form>
  );
}

export function humaniseLoginError(status: number, detail: string): string {
  if (status === 401 && detail === "invalid credentials") {
    return "Email or password is incorrect.";
  }
  if (status === 429) {
    return "Too many failed attempts. Try again in a minute.";
  }
  if (status === 503 && detail === "login is disabled in single-user dev mode") {
    return "Sign-in is disabled in single-user dev mode.";
  }
  return detail || `Sign-in failed (HTTP ${status}).`;
}
