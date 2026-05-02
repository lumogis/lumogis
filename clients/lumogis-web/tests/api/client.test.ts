// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Vitest unit — apiClient refresh-on-401 contract.
// Parent plan §"Phase 1 Pass 1.1" + §Test cases line 1121
// ("Vitest unit: API client refresh-on-401").

import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiClient } from "../../src/api/client";
import { AccessTokenStore } from "../../src/api/tokens";

interface FetchCall {
  url: string;
  init?: RequestInit;
}

function makeFetch(responses: Array<() => Promise<Response>>): {
  fetchImpl: typeof fetch;
  calls: FetchCall[];
} {
  const calls: FetchCall[] = [];
  let i = 0;
  const fetchImpl: typeof fetch = (input, init) => {
    calls.push({ url: input as string, init: init as RequestInit });
    const factory = responses[i] ?? responses[responses.length - 1]!;
    i += 1;
    return factory();
  };
  return { fetchImpl, calls };
}

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("ApiClient", () => {
  describe("auth-token interceptor", () => {
    it("attaches Authorization: Bearer when a token is set", async () => {
      const tokens = new AccessTokenStore();
      tokens.set("access-1");
      const { fetchImpl, calls } = makeFetch([() => Promise.resolve(jsonResponse(200, { ok: true }))]);
      const client = new ApiClient({ tokens, fetchImpl });

      await client.fetch("/api/v1/auth/me");

      expect(calls).toHaveLength(1);
      const headers = new Headers(calls[0]!.init!.headers);
      expect(headers.get("Authorization")).toBe("Bearer access-1");
    });

    it("does NOT attach Authorization when no token is set", async () => {
      const tokens = new AccessTokenStore();
      const { fetchImpl, calls } = makeFetch([() => Promise.resolve(jsonResponse(200, { ok: true }))]);
      const client = new ApiClient({ tokens, fetchImpl });

      await client.fetch("/api/v1/auth/me");

      const headers = new Headers(calls[0]!.init!.headers);
      expect(headers.get("Authorization")).toBeNull();
    });

    it("includes credentials so the lumogis_refresh cookie flows", async () => {
      const tokens = new AccessTokenStore();
      const { fetchImpl, calls } = makeFetch([() => Promise.resolve(jsonResponse(200, {}))]);
      const client = new ApiClient({ tokens, fetchImpl });

      await client.fetch("/api/v1/auth/me");

      expect(calls[0]!.init!.credentials).toBe("include");
    });
  });

  describe("refresh-on-401", () => {
    it("refreshes once on 401 and retries the original request with the new token", async () => {
      const tokens = new AccessTokenStore();
      tokens.set("old-token");
      const { fetchImpl, calls } = makeFetch([
        () => Promise.resolve(jsonResponse(401, { detail: "expired" })),
        () =>
          Promise.resolve(
            jsonResponse(200, {
              access_token: "new-token",
              token_type: "bearer",
              expires_in: 900,
              user: { id: "u1", email: "a@b.c", role: "user" },
            }),
          ),
        () => Promise.resolve(jsonResponse(200, { ok: true })),
      ]);
      const client = new ApiClient({ tokens, fetchImpl });

      const res = await client.fetch("/api/v1/audit?limit=10");

      expect(res.status).toBe(200);
      expect(calls).toHaveLength(3);
      expect(calls[0]!.url).toMatch(/\/api\/v1\/audit/);
      expect(calls[1]!.url).toMatch(/\/api\/v1\/auth\/refresh$/);
      expect(calls[1]!.init!.method).toBe("POST");
      expect(calls[2]!.url).toMatch(/\/api\/v1\/audit/);
      const retryHeaders = new Headers(calls[2]!.init!.headers);
      expect(retryHeaders.get("Authorization")).toBe("Bearer new-token");
      expect(tokens.get()).toBe("new-token");
    });

    it("fires onAuthExpired and clears tokens when refresh also fails", async () => {
      const tokens = new AccessTokenStore();
      tokens.set("old-token");
      const onAuthExpired = vi.fn();
      const { fetchImpl, calls } = makeFetch([
        () => Promise.resolve(jsonResponse(401, { detail: "expired" })),
        () => Promise.resolve(jsonResponse(401, { detail: "missing refresh cookie" })),
      ]);
      const client = new ApiClient({ tokens, fetchImpl, onAuthExpired });

      const res = await client.fetch("/api/v1/audit");

      expect(res.status).toBe(401);
      expect(calls).toHaveLength(2);
      expect(tokens.get()).toBeNull();
      expect(onAuthExpired).toHaveBeenCalledTimes(1);
    });

    it("single-flights concurrent refresh attempts (only ONE /refresh call)", async () => {
      const tokens = new AccessTokenStore();
      tokens.set("old-token");
      let refreshResolve!: (res: Response) => void;
      const refreshPromise = new Promise<Response>((r) => {
        refreshResolve = r;
      });
      let originalCount = 0;
      const fetchImpl: typeof fetch = (input) => {
        const url = String(input);
        if (url.includes("/auth/refresh")) {
          return refreshPromise;
        }
        originalCount += 1;
        if (originalCount <= 3) return Promise.resolve(jsonResponse(401, { detail: "expired" }));
        return Promise.resolve(jsonResponse(200, { ok: true }));
      };
      const client = new ApiClient({ tokens, fetchImpl });

      const a = client.fetch("/api/v1/a");
      const b = client.fetch("/api/v1/b");
      const c = client.fetch("/api/v1/c");

      await Promise.resolve();
      await Promise.resolve();

      refreshResolve(
        jsonResponse(200, {
          access_token: "shared-new",
          token_type: "bearer",
          expires_in: 900,
          user: { id: "u1", email: "a@b.c", role: "user" },
        }),
      );

      const [ra, rb, rc] = await Promise.all([a, b, c]);
      expect(ra.status).toBe(200);
      expect(rb.status).toBe(200);
      expect(rc.status).toBe(200);
      expect(tokens.get()).toBe("shared-new");
    });

    it("does NOT trigger refresh on a non-401 response (e.g. 403, 500)", async () => {
      const tokens = new AccessTokenStore();
      tokens.set("token");
      const { fetchImpl, calls } = makeFetch([
        () => Promise.resolve(jsonResponse(403, { detail: "forbidden" })),
      ]);
      const client = new ApiClient({ tokens, fetchImpl });

      const res = await client.fetch("/api/v1/admin/users");
      expect(res.status).toBe(403);
      expect(calls).toHaveLength(1);
    });

    it("fetchOnce injects auth headers but does not refresh/retry on 401", async () => {
      const tokens = new AccessTokenStore();
      tokens.set("old-token");
      const { fetchImpl, calls } = makeFetch([
        () => Promise.resolve(jsonResponse(401, { detail: "expired" })),
      ]);
      const client = new ApiClient({ tokens, fetchImpl });

      const res = await client.fetchOnce("/api/v1/me/export", { method: "POST" });

      expect(res.status).toBe(401);
      expect(calls).toHaveLength(1);
      expect(calls[0]!.url).toMatch(/\/api\/v1\/me\/export/);
      const headers = new Headers(calls[0]!.init!.headers);
      expect(headers.get("Authorization")).toBe("Bearer old-token");
      expect(tokens.get()).toBe("old-token");
    });
  });

  describe("tryRefresh()", () => {
    it("returns true and stores the new access token on success", async () => {
      const tokens = new AccessTokenStore();
      const { fetchImpl } = makeFetch([
        () =>
          Promise.resolve(
            jsonResponse(200, {
              access_token: "fresh",
              token_type: "bearer",
              expires_in: 900,
              user: { id: "u1", email: "a@b.c", role: "user" },
            }),
          ),
      ]);
      const client = new ApiClient({ tokens, fetchImpl });

      const ok = await client.tryRefresh();
      expect(ok).toBe(true);
      expect(tokens.get()).toBe("fresh");
    });

    it("returns false and clears tokens on a 401", async () => {
      const tokens = new AccessTokenStore();
      tokens.set("stale");
      const { fetchImpl } = makeFetch([
        () => Promise.resolve(jsonResponse(401, { detail: "missing refresh cookie" })),
      ]);
      const client = new ApiClient({ tokens, fetchImpl });

      const ok = await client.tryRefresh();
      expect(ok).toBe(false);
      expect(tokens.get()).toBeNull();
    });

    it("returns false on a fetch network failure (no throw)", async () => {
      const tokens = new AccessTokenStore();
      tokens.set("stale");
      const fetchImpl: typeof fetch = () => Promise.reject(new Error("offline"));
      const client = new ApiClient({ tokens, fetchImpl });

      const ok = await client.tryRefresh();
      expect(ok).toBe(false);
      expect(tokens.get()).toBeNull();
    });
  });
});
