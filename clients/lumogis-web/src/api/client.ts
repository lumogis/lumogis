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
    const first = await this.fetchImpl(url, this.withAuthHeaders(init));
    if (first.status !== 401) return first;

    // Only auto-refresh when there was a previous session to recover; on a
    // first-load 401 (no token) `tryRefresh` will still attempt the cookie
    // path and fail cleanly to onAuthExpired without an extra retry.
    const refreshed = await this.tryRefresh();
    if (!refreshed) {
      this.onAuthExpired();
      return first;
    }
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
   */
  async tryRefresh(): Promise<boolean> {
    if (this.refreshInFlight) return this.refreshInFlight;
    const url = this.resolveUrl("/api/v1/auth/refresh");
    this.refreshInFlight = (async () => {
      try {
        const res = await this.fetchImpl(url, {
          method: "POST",
          credentials: "include",
        });
        if (!res.ok) {
          this.tokens.clear();
          return false;
        }
        const body = (await res.json()) as LoginResponse;
        if (typeof body?.access_token !== "string" || body.access_token.length === 0) {
          this.tokens.clear();
          return false;
        }
        this.tokens.set(body.access_token);
        return true;
      } catch {
        this.tokens.clear();
        return false;
      } finally {
        this.refreshInFlight = null;
      }
    })();
    return this.refreshInFlight;
  }

  /** Convenience: GET + JSON parse. Throws on non-2xx after refresh-retry. */
  async getJson<T>(path: string, init?: RequestInit): Promise<T> {
    const res = await this.fetch(path, { ...init, method: "GET" });
    if (!res.ok) throw new ApiError(res.status, await safeReadDetail(res));
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
    if (!res.ok) throw new ApiError(res.status, await safeReadDetail(res));
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
    if (!res.ok) throw new ApiError(res.status, await safeReadDetail(res));
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
    if (!res.ok) throw new ApiError(res.status, await safeReadDetail(res));
    const t = await res.text();
    if (t.length === 0) return undefined as TRes;
    return JSON.parse(t) as TRes;
  }

  /** DELETE; parses JSON if response has a body, else void. */
  async delete<TRes = void>(path: string, init?: RequestInit): Promise<TRes> {
    const res = await this.fetch(path, { ...init, method: "DELETE" });
    if (!res.ok) throw new ApiError(res.status, await safeReadDetail(res));
    const t = await res.text();
    if (t.length === 0) return undefined as TRes;
    return JSON.parse(t) as TRes;
  }
}

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly detail: string,
  ) {
    super(`HTTP ${status}: ${detail}`);
    this.name = "ApiError";
  }
}

async function safeReadDetail(res: Response): Promise<string> {
  try {
    const body = (await res.clone().json()) as { detail?: unknown };
    if (typeof body?.detail === "string") return body.detail;
    return JSON.stringify(body);
  } catch {
    try {
      return await res.text();
    } catch {
      return res.statusText || "request failed";
    }
  }
}
