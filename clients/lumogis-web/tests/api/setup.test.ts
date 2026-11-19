// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-981 — the two public setup routes, and the absolute invite link helper.

import { afterEach, describe, expect, it, vi } from "vitest";

import { absoluteInviteUrl, isLoopbackHost } from "../../src/api/invites";
import { SetupClaimError, claimSetup, getSetupStatus } from "../../src/api/setup";

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("getSetupStatus", () => {
  it("A1 reads needs_setup true and false from a 200", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => json(200, { needs_setup: true })));
    expect(await getSetupStatus()).toBe("needed");
    vi.stubGlobal("fetch", vi.fn(async () => json(200, { needs_setup: false })));
    expect(await getSetupStatus()).toBe("not_needed");
  });

  it("A2 a 404 is not_needed; every other failure is unreachable", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => json(404, { detail: "Not Found" })));
    expect(await getSetupStatus()).toBe("not_needed");
    for (const status of [401, 500, 502, 503]) {
      vi.stubGlobal("fetch", vi.fn(async () => json(status, { detail: "x" })));
      expect(await getSetupStatus()).toBe("unreachable");
    }
    vi.stubGlobal("fetch", vi.fn(async () => Promise.reject(new TypeError("network"))));
    expect(await getSetupStatus()).toBe("unreachable");
    vi.stubGlobal("fetch", vi.fn(async () => json(200, { unexpected: 1 })));
    expect(await getSetupStatus()).toBe("unreachable");
  });

  it("A2 gives up after 5 s", async () => {
    vi.useFakeTimers();
    try {
      vi.stubGlobal(
        "fetch",
        vi.fn(
          (_url: string, init?: RequestInit) =>
            new Promise<Response>((_res, rej) => {
              init?.signal?.addEventListener("abort", () => rej(new DOMException("aborted", "AbortError")));
            }),
        ),
      );
      const p = getSetupStatus();
      await vi.advanceTimersByTimeAsync(5000);
      expect(await p).toBe("unreachable");
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("claimSetup", () => {
  it("A3 posts JSON with credentials included and returns the login response", async () => {
    const login = { access_token: "a", token_type: "bearer", expires_in: 900, user: { id: "u", email: "a@b.co", role: "admin" } };
    const fetchMock = vi.fn(async (_url: string, _init?: RequestInit) => json(200, login));
    vi.stubGlobal("fetch", fetchMock);
    const res = await claimSetup("a@b.co", "correct-horse-battery");
    expect(res).toEqual(login);
    const [url, init] = fetchMock.mock.calls[0]!;
    expect(url).toBe("/api/v1/setup/claim");
    expect(init?.method).toBe("POST");
    expect(init?.credentials).toBe("include");
    expect(JSON.parse(String(init?.body))).toEqual({ email: "a@b.co", password: "correct-horse-battery" });
  });

  it("A3 a non-2xx throws SetupClaimError with the status, string detail and body", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => json(409, { detail: "setup is not open" })));
    const err = await claimSetup("a@b.co", "x").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(SetupClaimError);
    expect((err as SetupClaimError).status).toBe(409);
    expect((err as SetupClaimError).detail).toBe("setup is not open");
    expect((err as SetupClaimError).body).toEqual({ detail: "setup is not open" });
  });

  it("A3 an array detail (Pydantic) gives detail null and keeps the body", async () => {
    const body = { detail: [{ loc: ["body", "email"], msg: "bad", type: "value_error" }] };
    vi.stubGlobal("fetch", vi.fn(async () => json(422, body)));
    const err = (await claimSetup("x", "y").catch((e: unknown) => e)) as SetupClaimError;
    expect(err.status).toBe(422);
    expect(err.detail).toBeNull();
    expect(err.body).toEqual(body);
  });

  it("A3 a body that is not JSON still throws SetupClaimError", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("<html>", { status: 502 })));
    const err = (await claimSetup("x", "y").catch((e: unknown) => e)) as SetupClaimError;
    expect(err).toBeInstanceOf(SetupClaimError);
    expect(err.status).toBe(502);
    expect(err.detail).toBeNull();
    expect(err.body).toBeNull();
  });
});

describe("absoluteInviteUrl", () => {
  it("A4 prefixes the browser origin to a relative link and leaves an absolute one alone", () => {
    expect(absoluteInviteUrl("/invite?token=x")).toBe(`${window.location.origin}/invite?token=x`);
    expect(absoluteInviteUrl("https://lumogis.example.com/invite?token=x")).toBe(
      "https://lumogis.example.com/invite?token=x",
    );
  });

  it("A4 isLoopbackHost knows localhost and loopback addresses only", () => {
    for (const h of ["localhost", "LOCALHOST", "127.0.0.1", "127.1.2.3", "::1", "[::1]", "a.localhost"]) {
      expect(isLoopbackHost(h)).toBe(true);
    }
    for (const h of ["192.168.1.20", "lumogis.lan", "example.com", "127.0.0.1.evil.com", ""]) {
      expect(isLoopbackHost(h)).toBe(false);
    }
  });
});
