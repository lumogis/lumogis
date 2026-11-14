// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Typed fetch wrapper with auth-token interceptor and 401-refresh logic.
// Implements parent plan §"Phase 1 Pass 1.1 item 2" + §Codebase context
// auth contract (line 95-96, 136, 1026-1055).
//
// Behaviour:
//
//  * Every request gets `credentials: "include"` so the HttpOnly `lumogis_refresh`
//    cookie (Path=/api/v1/auth, SameSite=Strict) flows on `/api/v1/auth/*` calls.
//  * Every request with a stored access token gets `Authorization: Bearer <jwt>`.
//  * On any 401 response, ApiClient single-flight-calls `POST /api/v1/auth/refresh`.
//    If refresh succeeds, the original request is retried ONCE with the new
//    access token. If refresh fails (the cookie is gone, the JTI was rotated
//    by another device, the user was disabled, or the refresh JWT expired),
//    the access-token store is cleared and `onAuthExpired` fires so
//    AuthProvider can flip the UI to anonymous.
//  * Multiple concurrent 401s during a single refresh window share the in-flight
//    refresh promise so we never burst the `/refresh` endpoint.

import type { LoginResponse } from "./auth";
import { AccessTokenStore } from "./tokens";

export interface ApiClientOptions {
  /** Base URL prefix for relative paths (default `""` → same-origin). */
  baseUrl?: string;
  tokens: AccessTokenStore;
  /** Fired when refresh-on-401 fails (i.e. the user has lost their session). */
  onAuthExpired?: () => void;
  /** Inject `fetch` for tests. Defaults to `globalThis.fetch`. */
  fetchImpl?: typeof fetch;
}

export class ApiClient {
  private readonly baseUrl: string;
  private readonly tokens: AccessTokenStore;
  private onAuthExpired: () => void;
  private readonly fetchImpl: typeof fetch;
  private refreshInFlight: Promise<boolean> | null = null;
  /** The session member the in-flight refresh was started for (LUM-886). */
  private refreshStartedAs: string | null = null;
  private sessionUserId: string | null = null;

  constructor(opts: ApiClientOptions) {
    this.baseUrl = opts.baseUrl ?? "";
    this.tokens = opts.tokens;
    this.onAuthExpired = opts.onAuthExpired ?? (() => {});
    this.fetchImpl =
      opts.fetchImpl ??
      ((input, init) =>
        // Bind to globalThis so jsdom fetch isn't called with a detached `this`.
        globalThis.fetch(input as RequestInfo, init as RequestInit));
  }

  setAuthExpiredHandler(fn: () => void): void {
    this.onAuthExpired = fn;
  }

  /**
   * Remember which member this client's session belongs to (LUM-886, D11/D14/D15).
   * `AuthProvider` sets it after the sign-in reconcile and clears it at sign-out and
   * before a new member's token is stored. While it is set, a refresh that comes back
   * as someone else is refused (see {@link tryRefresh}), and the capture outbox sync
   * stops when it no longer matches the rows' owner.
   */
  setSessionUserId(id: string | null): void {
    this.sessionUserId = id;
  }

  /** The member this client's live session belongs to, or `null` (D14). */
  getSessionUserId(): string | null {
    return this.sessionUserId;
  }

  private resolveUrl(path: string): string {
    if (path.startsWith("http://") || path.startsWith("https://")) return path;
    if (path.startsWith("/")) return `${this.baseUrl}${path}`;
    return `${this.baseUrl}/${path}`;
  }

  private withAuthHeaders(init: RequestInit | undefined): RequestInit {
    const headers = new Headers(init?.headers);
    const token = this.tokens.get();
    if (token && !headers.has("Authorization")) {
      headers.set("Authorization", `Bearer ${token}`);
    }
    return { credentials: "include", ...init, headers };
  }

  /**
   * Issue a request with `Authorization: Bearer` injection + automatic
   * 401-refresh-and-retry. Returns the final Response (the post-retry
   * response if refresh succeeded, or the original 401 otherwise).
   */
  async fetch(path: string, init?: RequestInit): Promise<Response> {
    const url = this.resolveUrl(path);
    // The member this request is sent as. A refresh may only be replayed for the same
    // member: after a sign-out and a different sign-in, the retry would carry the new
    // member's token for a request the previous member built (LUM-886, D11/D14).
    const sentAs = this.sessionUserId;
    const first = await this.fetchImpl(url, this.withAuthHeaders(init));
    if (first.status !== 401) return first;
    if (this.sessionUserId !== sentAs) return first; // 401 for the previous member's request

    // Only auto-refresh when there was a previous session to recover; on a
    // first-load 401 (no token) `tryRefresh` will still attempt the cookie
    // path and fail cleanly to onAuthExpired without an extra retry.
    const refreshed = await this.tryRefresh();
    if (!refreshed) {
      // A refresh that failed because the session changed meanwhile belongs to the
      // previous member's request: do not sign the NEW session out (LUM-886 verify).
      if (this.sessionUserId !== sentAs) return first;
      this.onAuthExpired();
      return first;
    }
    // The session changed while the refresh ran: do not replay, and do not expire the
    // NEW session either — this 401 belongs to the previous member's request.
    if (this.sessionUserId !== sentAs) return first;
    return this.fetchImpl(url, this.withAuthHeaders(init));
  }

  /**
   * Issue a single authenticated request without the 401 refresh/retry path.
   * Use for streaming/download endpoints where retrying can hide a partial or
   * unsafe transfer boundary from the UI.
   */
  async fetchOnce(path: string, init?: RequestInit): Promise<Response> {
    return this.fetchImpl(this.resolveUrl(path), this.withAuthHeaders(init));
  }

  /**
   * Attempt to mint a new access token from the refresh cookie. Returns true
   * iff a new access token landed in `tokens`. Single-flight: concurrent
   * callers share the same in-flight promise.
   *
   * LUM-886 (D11/D15): when a session member is set and the refresh body's `user.id`
   * is missing or differs, the refresh cookie now belongs to somebody else (another
   * tab signed them in). The token is NOT stored, the store is cleared and `false` is
   * returned, so `fetch` signs this tab out instead of replaying the request as the
   * other member. With no session member set (boot) behaviour is unchanged.
   */
  async tryRefresh(): Promise<boolean> {
    // Share an in-flight refresh only within the same session: a refresh started for the
    // previous member must not decide the next member's 401s (LUM-886 verify).
    if (this.refreshInFlight && this.refreshStartedAs === this.sessionUserId) return this.refreshInFlight;
    const url = this.resolveUrl("/api/v1/auth/refresh");
    const startedAs = this.sessionUserId;
    // Only the session that started this refresh may clear the token store.
    const clearIfSameSession = (): void => {
      if (this.sessionUserId === startedAs) this.tokens.clear();
    };
    let run: Promise<boolean> | undefined = undefined;
    run = (async () => {
      // Yield once so `run` is assigned before `finally` can compare against it.
      await Promise.resolve();
      try {
        const res = await this.fetchImpl(url, {
          method: "POST",
          credentials: "include",
        });
        if (!res.ok) {
          clearIfSameSession();
          return false;
        }
        const body = (await res.json()) as LoginResponse;
        if (typeof body?.access_token !== "string" || body.access_token.length === 0) {
          clearIfSameSession();
          return false;
        }
        if (this.sessionUserId !== startedAs) {
          // The session changed (sign-out, another sign-in) while this refresh ran: its
          // token belongs to a session that no longer exists here. Drop it.
          return false;
        }
        if (this.sessionUserId !== null && body.user?.id !== this.sessionUserId) {
          this.tokens.clear();
          return false;
        }
        this.tokens.set(body.access_token);
        return true;
      } catch {
        clearIfSameSession();
        return false;
      } finally {
        if (this.refreshInFlight === run) this.refreshInFlight = null;
      }
    })();
    this.refreshInFlight = run;
    this.refreshStartedAs = startedAs;
    return this.refreshInFlight;
  }

  /** Convenience: GET + JSON parse. Throws on non-2xx after refresh-retry. */
  async getJson<T>(path: string, init?: RequestInit): Promise<T> {
    const res = await this.fetch(path, { ...init, method: "GET" });
    if (!res.ok) throw await apiErrorFrom(res);
    return (await res.json()) as T;
  }

  /** Convenience: POST + JSON body + JSON parse. */
  async postJson<TReq, TRes>(path: string, body: TReq, init?: RequestInit): Promise<TRes> {
    const headers = new Headers(init?.headers);
    headers.set("Content-Type", "application/json");
    const res = await this.fetch(path, {
      ...init,
      method: "POST",
      headers,
      body: JSON.stringify(body),
    });
    if (!res.ok) throw await apiErrorFrom(res);
    return (await res.json()) as TRes;
  }

  /** PUT + JSON body + JSON parse. */
  async putJson<TReq, TRes>(path: string, body: TReq, init?: RequestInit): Promise<TRes> {
    const headers = new Headers(init?.headers);
    headers.set("Content-Type", "application/json");
    const res = await this.fetch(path, {
      ...init,
      method: "PUT",
      headers,
      body: JSON.stringify(body),
    });
    if (!res.ok) throw await apiErrorFrom(res);
    const t = await res.text();
    if (t.length === 0) return undefined as TRes;
    return JSON.parse(t) as TRes;
  }

  /** PATCH + JSON body + JSON parse. */
  async patchJson<TReq, TRes>(path: string, body: TReq, init?: RequestInit): Promise<TRes> {
    const headers = new Headers(init?.headers);
    headers.set("Content-Type", "application/json");
    const res = await this.fetch(path, {
      ...init,
      method: "PATCH",
      headers,
      body: JSON.stringify(body),
    });
    if (!res.ok) throw await apiErrorFrom(res);
    const t = await res.text();
    if (t.length === 0) return undefined as TRes;
    return JSON.parse(t) as TRes;
  }

  /** DELETE; parses JSON if response has a body, else void. */
  async delete<TRes = void>(path: string, init?: RequestInit): Promise<TRes> {
    const res = await this.fetch(path, { ...init, method: "DELETE" });
    if (!res.ok) throw await apiErrorFrom(res);
    const t = await res.text();
    if (t.length === 0) return undefined as TRes;
    return JSON.parse(t) as TRes;
  }
}

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly detail: string,
    /**
     * The route's machine-readable `code`, when the body carried one
     * (`{"detail": {"code": "…"}}`) — LUM-759.
     *
     * Additive and optional: `detail` is unchanged for every existing caller,
     * and callers that never look at `code` behave exactly as before. Key UI
     * copy on this, never on `detail` or `message` — see the note on
     * `safeReadDetail` below.
     */
    public readonly code?: string,
  ) {
    super(`HTTP ${status}: ${detail}`);
    this.name = "ApiError";
  }
}

/**
 * Read an error body into `(detail, code)`.
 *
 * **`detail` must stay byte-identical `JSON.stringify(body)` for an object
 * detail.** `AdminSharedItemsView.tsx` does `JSON.parse(e.detail)` and reads
 * `parsed?.detail?.message` to surface governance failures — so stringifying
 * `body.detail` instead, or substituting the extracted message, silently turns
 * that page's messages into raw blobs. `code` was added alongside rather than
 * by reshaping `detail` for exactly that reason (LUM-759).
 *
 * Note `emailAttachments.ts:readErrorPayload` stringifies `body.detail` where
 * this stringifies `body`. The two disagree; do not "harmonise" them without
 * auditing every consumer.
 */
async function safeReadDetail(res: Response): Promise<{ detail: string; code?: string }> {
  try {
    const body = (await res.clone().json()) as { detail?: unknown };
    const inner = body?.detail;
    const code =
      inner && typeof inner === "object" && typeof (inner as { code?: unknown }).code === "string"
        ? ((inner as { code: string }).code)
        : undefined;
    if (typeof inner === "string") return { detail: inner };
    return { detail: JSON.stringify(body), code };
  } catch {
    try {
      return { detail: await res.text() };
    } catch {
      return { detail: res.statusText || "request failed" };
    }
  }
}

/**
 * Build an `ApiError` from a non-2xx response, preserving `detail` exactly.
 *
 * Exported (LUM-849) because a surface module that needs the raw `Response` —
 * to read a header, say — cannot use `getJson`, which discards it. Such a module
 * must still produce the SAME error object every other caller produces: the
 * shared error UI keys on `ApiError.status` and `.code`, so a module that throws
 * a bare `Error` silently downgrades every failure on its page to the generic
 * arm. Use this rather than inventing an error shape.
 */
export async function apiErrorFrom(res: Response): Promise<ApiError> {
  const { detail, code } = await safeReadDetail(res);
  return new ApiError(res.status, detail, code);
}
