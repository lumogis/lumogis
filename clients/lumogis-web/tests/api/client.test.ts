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

// LUM-759 — `ApiError.code` surfaces the route's machine-readable code so UI
// copy can key on it without re-parsing a stringified envelope.
describe("ApiError.code (LUM-759)", () => {
  function failWith(status: number, body: unknown): typeof fetch {
    return (async () =>
      new Response(JSON.stringify(body), {
        status,
        headers: { "Content-Type": "application/json" },
      })) as unknown as typeof fetch;
  }

  it("an object detail exposes its code", async () => {
    const client = new ApiClient({
      tokens: new AccessTokenStore(),
      fetchImpl: failWith(503, { detail: { error: "mailbox_unreachable", code: "mailbox_unreachable" } }),
    });
    await expect(client.putJson("/x", {})).rejects.toMatchObject({
      status: 503,
      code: "mailbox_unreachable",
    });
  });

  it("an object detail's string is still the whole envelope", async () => {
    // THE byte-identity guard. AdminSharedItemsView.tsx does
    // `JSON.parse(e.detail)` then reads `parsed?.detail?.message` — its own
    // comment documents the dependency on safeReadDetail stringifying the WHOLE
    // body. An implementer who has just taught it to parse the body will
    // reasonably think to put something readable in `detail`; that silently
    // turns that page's governance messages into raw blobs, and every other
    // test here would still pass.
    const body = { detail: { code: "unshare_partial", message: "Couldn't fully unshare — please retry" } };
    const client = new ApiClient({ tokens: new AccessTokenStore(), fetchImpl: failWith(409, body) });
    try {
      await client.postJson("/x", {});
      throw new Error("expected a rejection");
    } catch (e) {
      const err = e as { detail: string };
      expect(err.detail).toBe(JSON.stringify(body));
      // Replay AdminSharedItemsView's extraction verbatim.
      const parsed = JSON.parse(err.detail) as { detail?: { message?: unknown } };
      expect(parsed?.detail?.message).toBe("Couldn't fully unshare — please retry");
    }
  });

  it("a string detail leaves detail unchanged and code undefined", async () => {
    const client = new ApiClient({
      tokens: new AccessTokenStore(),
      fetchImpl: failWith(422, { detail: "plain string reason" }),
    });
    await expect(client.postJson("/x", {})).rejects.toMatchObject({
      detail: "plain string reason",
      code: undefined,
    });
  });

  it("an unparseable body does not throw", async () => {
    const client = new ApiClient({
      tokens: new AccessTokenStore(),
      fetchImpl: (async () =>
        new Response("<html>gateway</html>", { status: 502 })) as unknown as typeof fetch,
    });
    await expect(client.getJson("/x")).rejects.toMatchObject({
      status: 502,
      detail: "<html>gateway</html>",
      code: undefined,
    });
  });
});

describe("session member identity on refresh (LUM-886, D11/D15)", () => {
  function setup(sessionUser: string | null, refreshUser: string | null | undefined) {
    const tokens = new AccessTokenStore();
    tokens.set("old-token");
    const seen: Array<string | null> = [];
    tokens.subscribe(() => seen.push(tokens.get()));
    const { fetchImpl, calls } = makeFetch([
      () => Promise.resolve(jsonResponse(401, { detail: "expired" })),
      () =>
        Promise.resolve(
          jsonResponse(
            200,
            refreshUser === undefined
              ? { access_token: "new-token" }
              : { access_token: "new-token", user: { id: refreshUser } },
          ),
        ),
      () => Promise.resolve(jsonResponse(200, { ok: true })),
    ]);
    const onAuthExpired = vi.fn();
    const client = new ApiClient({ tokens, fetchImpl, onAuthExpired });
    if (sessionUser !== null) client.setSessionUserId(sessionUser);
    return { tokens, calls, onAuthExpired, client, seen };
  }

  it("refresh as the same member replays the request", async () => {
    const { client, calls, onAuthExpired, tokens } = setup("u-a", "u-a");
    const res = await client.fetch("/api/v1/x");
    expect(res.status).toBe(200);
    expect(calls).toHaveLength(3);
    expect(onAuthExpired).not.toHaveBeenCalled();
    expect(tokens.get()).toBe("new-token");
  });

  it("refresh as a different member does not replay and never stores the token", async () => {
    const { client, calls, onAuthExpired, tokens, seen } = setup("u-a", "u-b");
    const res = await client.fetch("/api/v1/x", { method: "POST" });
    expect(res.status).toBe(401);
    expect(calls).toHaveLength(2);
    expect(calls[1]!.url).toContain("/api/v1/auth/refresh");
    expect(onAuthExpired).toHaveBeenCalledTimes(1);
    expect(tokens.get()).toBeNull();
    expect(seen).not.toContain("new-token");
  });

  it("getSessionUserId returns what setSessionUserId stored", () => {
    const client = new ApiClient({ tokens: new AccessTokenStore() });
    expect(client.getSessionUserId()).toBeNull();
    client.setSessionUserId("u-a");
    expect(client.getSessionUserId()).toBe("u-a");
    client.setSessionUserId(null);
    expect(client.getSessionUserId()).toBeNull();
  });

  it("a refresh body without user fails closed while a session member is set", async () => {
    const { client, calls, onAuthExpired, tokens } = setup("u-a", undefined);
    const res = await client.fetch("/api/v1/x");
    expect(res.status).toBe(401);
    expect(calls).toHaveLength(2);
    expect(onAuthExpired).toHaveBeenCalledTimes(1);
    expect(tokens.get()).toBeNull();
  });

  it("no session member set (boot) keeps today's behaviour", async () => {
    const { client, calls, onAuthExpired, tokens } = setup(null, "u-b");
    const res = await client.fetch("/api/v1/x");
    expect(res.status).toBe(200);
    expect(calls).toHaveLength(3);
    expect(onAuthExpired).not.toHaveBeenCalled();
    expect(tokens.get()).toBe("new-token");
  });
});

describe("a 401 for the previous member's request (LUM-886 review)", () => {
  it("is not replayed with the next member's token after a sign-out and another sign-in", async () => {
    const tokens = new AccessTokenStore();
    tokens.set("tok-a");
    let release!: () => void;
    const held = new Promise<void>((r) => {
      release = r;
    });
    const sent: Array<string | null> = [];
    const fetchImpl: typeof fetch = async (input, init) => {
      const url = String(input);
      if (url.includes("/auth/refresh")) {
        return jsonResponse(200, { access_token: "tok-b", user: { id: "u-b" } });
      }
      sent.push(new Headers((init as RequestInit).headers).get("Authorization"));
      if (sent.length === 1) {
        await held;
        return jsonResponse(401, { detail: "expired" });
      }
      return jsonResponse(200, { ok: true });
    };
    const onAuthExpired = vi.fn();
    const client = new ApiClient({ tokens, fetchImpl, onAuthExpired });
    client.setSessionUserId("u-a");

    const pending = client.fetch("/api/v1/captures", { method: "POST" });
    // A signs out, B signs in on the same client while A's request is in flight.
    client.setSessionUserId(null);
    tokens.set("tok-b");
    client.setSessionUserId("u-b");
    release();
    const res = await pending;

    expect(res.status).toBe(401);
    expect(sent).toEqual(["Bearer tok-a"]); // never replayed, never sent as B
    expect(onAuthExpired).not.toHaveBeenCalled(); // and B's session is not expired
  });

  it("a refresh that finishes after the session changed is dropped", async () => {
    const tokens = new AccessTokenStore();
    tokens.set("tok-a");
    let release!: () => void;
    const held = new Promise<void>((r) => {
      release = r;
    });
    const fetchImpl: typeof fetch = async () => {
      await held;
      return jsonResponse(200, { access_token: "tok-late", user: { id: "u-a" } });
    };
    const client = new ApiClient({ tokens, fetchImpl });
    client.setSessionUserId("u-a");

    const refreshing = client.tryRefresh();
    client.setSessionUserId(null); // signed out while the refresh was in flight
    tokens.clear();
    release();

    expect(await refreshing).toBe(false);
    expect(tokens.get()).toBeNull();
  });
});

describe("a refresh that fails across a session change (LUM-886 verify)", () => {
  it("does not sign the next member out or clear their token", async () => {
    const tokens = new AccessTokenStore();
    tokens.set("tok-a");
    let releaseRefresh!: () => void;
    const refreshHeld = new Promise<void>((r) => {
      releaseRefresh = r;
    });
    const fetchImpl: typeof fetch = async (input) => {
      const url = String(input);
      if (url.includes("/auth/refresh")) {
        await refreshHeld;
        return jsonResponse(401, { detail: "revoked" });
      }
      return jsonResponse(401, { detail: "expired" });
    };
    const onAuthExpired = vi.fn();
    const client = new ApiClient({ tokens, fetchImpl, onAuthExpired });
    client.setSessionUserId("u-a");

    const aRequest = client.fetch("/api/v1/x");
    await vi.waitFor(() => expect(client["refreshInFlight"]).not.toBeNull());
    // A signs out and B signs in while A's refresh hangs.
    client.setSessionUserId(null);
    tokens.set("tok-b");
    client.setSessionUserId("u-b");
    releaseRefresh();

    const res = await aRequest;
    expect(res.status).toBe(401);
    expect(onAuthExpired).not.toHaveBeenCalled();
    expect(tokens.get()).toBe("tok-b");
  });

  it("the next member's 401 does not join the previous member's in-flight refresh", async () => {
    const tokens = new AccessTokenStore();
    tokens.set("tok-a");
    let releaseA!: () => void;
    const heldA = new Promise<void>((r) => {
      releaseA = r;
    });
    let refreshCalls = 0;
    const fetchImpl: typeof fetch = async (input) => {
      const url = String(input);
      if (url.includes("/auth/refresh")) {
        refreshCalls += 1;
        if (refreshCalls === 1) {
          await heldA;
          return jsonResponse(401, { detail: "revoked" });
        }
        return jsonResponse(200, { access_token: "tok-b2", user: { id: "u-b" } });
      }
      return jsonResponse(200, { ok: true });
    };
    const client = new ApiClient({ tokens, fetchImpl });
    client.setSessionUserId("u-a");
    const aRefresh = client.tryRefresh();

    client.setSessionUserId(null);
    tokens.set("tok-b");
    client.setSessionUserId("u-b");
    const bRefresh = client.tryRefresh();
    expect(bRefresh).not.toBe(aRefresh);
    expect(await bRefresh).toBe(true);
    releaseA();
    expect(await aRefresh).toBe(false);
    expect(refreshCalls).toBe(2);
    expect(tokens.get()).toBe("tok-b2");
  });
});
