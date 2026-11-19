// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
// LUM-331 — the device-side reachability check.

import { afterEach, describe, expect, it, vi } from "vitest";

import {
  canCheckFromHere,
  checkReachable,
  isHomeNetworkHost,
  isSecurePageOrigin,
} from "../../src/pwa/secureAddressCheck";

const URL_ = "https://kq7f3r2abcdefghijklmnopqrs.homes.lumogis.app";

describe("checkReachable", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it("an opaque answer means reachable; it asks no-cors without credentials", async () => {
    const f = vi.fn(async () => new Response(null, { status: 200 }));
    expect(await checkReachable(`${URL_}/`, 4000, f as unknown as typeof fetch)).toBe(true);
    const [target, init] = f.mock.calls[0] as unknown as [string, RequestInit];
    expect(target).toBe(`${URL_}/api/v1/health`);
    expect(init).toMatchObject({ mode: "no-cors", credentials: "omit", cache: "no-store" });
  });

  it("a network error (name blocked, bad certificate) means not reachable", async () => {
    const f = vi.fn(async () => {
      throw new TypeError("Failed to fetch");
    });
    expect(await checkReachable(URL_, 4000, f as unknown as typeof fetch)).toBe(false);
  });

  it("gives up after the timeout", async () => {
    vi.useFakeTimers();
    const f = vi.fn(
      (_: string, init: RequestInit) =>
        new Promise<Response>((_resolve, reject) => {
          init.signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
        }),
    );
    const p = checkReachable(URL_, 4000, f as unknown as typeof fetch);
    await vi.advanceTimersByTimeAsync(3999);
    let settled = false;
    void p.then(() => (settled = true));
    await Promise.resolve();
    expect(settled).toBe(false);
    await vi.advanceTimersByTimeAsync(1);
    expect(await p).toBe(false);
  });
});

describe("where the check may run", () => {
  it.each([
    ["localhost", true],
    ["127.0.0.1", true],
    ["192.168.1.20", true],
    ["10.0.0.7", true],
    ["172.16.0.1", true],
    ["172.31.255.1", true],
    ["172.32.0.1", false],
    ["100.64.0.1", false],
    ["lumogis.tailnet.ts.net", false],
    ["8.8.8.8", false],
  ])("%s → %s", (host, ok) => {
    expect(isHomeNetworkHost(host)).toBe(ok);
    expect(canCheckFromHere({ hostname: host })).toBe(ok);
  });

  it("https pages are secure origins", () => {
    expect(isSecurePageOrigin({ protocol: "https:" })).toBe(true);
    expect(isSecurePageOrigin({ protocol: "http:" })).toBe(false);
  });
});
