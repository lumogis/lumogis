// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Thomas Kohlborn, trading as Lumogis

import { describe, expect, it, vi } from "vitest";
import {
  buildMemorySearchUrl,
  fetchMemorySearchWithFetch,
  normaliseOrchestratorBaseUrl,
  parseMemorySearchResponse,
} from "./searchClient";

describe("normaliseOrchestratorBaseUrl", () => {
  it.each([
    ["http://localhost:8000/", "http://localhost:8000"],
    ["https://lumogis.lan///", "https://lumogis.lan"],
    ["  http://x:1  ", "http://x:1"],
  ])("normalises %s -> %s", (input, want) => {
    expect(normaliseOrchestratorBaseUrl(input)).toBe(want);
  });
});

describe("buildMemorySearchUrl", () => {
  it("builds search URL with limit 5", () => {
    const u = buildMemorySearchUrl("http://127.0.0.1:8000", "hello world");
    expect(u).toContain("/api/v1/memory/search");
    expect(u).toContain("limit=5");
    expect(u).toMatch(/q=hello(\+|%20)world/);
  });
});

describe("fetchMemorySearchWithFetch", () => {
  it("maps 200 + hits", async () => {
    const fetchMock = vi.fn(async () =>
      new Response(
        JSON.stringify({
          hits: [
            {
              id: "/tmp/a",
              score: 0.9,
              snippet: "sn",
              scope: "personal",
            },
          ],
          degraded: false,
        }),
        { status: 200, headers: { "content-type": "application/json" } },
      ),
    );
    const r = await fetchMemorySearchWithFetch(
      "http://localhost:8000",
      null,
      "q",
      new AbortController().signal,
      fetchMock as unknown as typeof fetch,
    );
    expect(r.hits).toHaveLength(1);
    expect(r.hits[0].id).toBe("/tmp/a");
    const call = fetchMock.mock.calls[0] as unknown as [string | URL, RequestInit];
    const [url, init] = call;
    expect(String(url)).toContain("/api/v1/memory/search");
    expect(init.headers).toBeDefined();
    expect((init.headers as Record<string, string>).Authorization).toBeUndefined();
  });

  it("sends Authorization when token non-empty", async () => {
    const fetchMock = vi.fn(async () =>
      new Response(JSON.stringify({ hits: [], degraded: false }), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );
    await fetchMemorySearchWithFetch(
      "http://localhost:8000",
      "tok",
      "q",
      new AbortController().signal,
      fetchMock as unknown as typeof fetch,
    );
    const call = fetchMock.mock.calls[0] as unknown as [string | URL, RequestInit];
    const init = call[1];
    expect((init.headers as Record<string, string>).Authorization).toBe("Bearer tok");
  });

  it("degraded banner payload", async () => {
    const fetchMock = vi.fn(async () =>
      new Response(JSON.stringify({ hits: [], degraded: true, reason: "embedder_not_ready" }), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );
    const r = await fetchMemorySearchWithFetch(
      "http://localhost:8000",
      null,
      "q",
      new AbortController().signal,
      fetchMock as unknown as typeof fetch,
    );
    expect(r.degraded).toBe(true);
    expect(r.reason).toBe("embedder_not_ready");
  });

  it("maps 401", async () => {
    const fetchMock = vi.fn(async () => new Response("{}", { status: 401 }));
    await expect(
      fetchMemorySearchWithFetch(
        "http://localhost:8000",
        null,
        "q",
        new AbortController().signal,
        fetchMock as unknown as typeof fetch,
      ),
    ).rejects.toThrow(/missing_or_invalid_session_token/);
  });

  it("maps 403", async () => {
    const fetchMock = vi.fn(async () => new Response("{}", { status: 403 }));
    await expect(
      fetchMemorySearchWithFetch(
        "http://localhost:8000",
        null,
        "q",
        new AbortController().signal,
        fetchMock as unknown as typeof fetch,
      ),
    ).rejects.toThrow(/forbidden/);
  });

  it("maps 429 and 5xx", async () => {
    const f429 = vi.fn(async () => new Response("{}", { status: 429 }));
    await expect(
      fetchMemorySearchWithFetch(
        "http://localhost:8000",
        null,
        "q",
        new AbortController().signal,
        f429 as unknown as typeof fetch,
      ),
    ).rejects.toThrow(/rate_limited/);

    const f500 = vi.fn(async () => new Response("{}", { status: 500 }));
    await expect(
      fetchMemorySearchWithFetch(
        "http://localhost:8000",
        null,
        "q",
        new AbortController().signal,
        f500 as unknown as typeof fetch,
      ),
    ).rejects.toThrow(/server_error/);
  });

  it("stack-down on fetch network rejection", async () => {
    const fetchMock = vi.fn(async () => {
      throw new TypeError("Failed to fetch");
    });
    await expect(
      fetchMemorySearchWithFetch(
        "http://localhost:8000",
        null,
        "q",
        new AbortController().signal,
        fetchMock as unknown as typeof fetch,
      ),
    ).rejects.toThrow(TypeError);
  });
});

describe("parseMemorySearchResponse", () => {
  it("rejects HTML-looking 200 non-json", async () => {
    const res = new Response("<html></html>", {
      status: 200,
      headers: { "content-type": "text/html" },
    });
    await expect(parseMemorySearchResponse(res)).rejects.toThrow(/non_json/);
  });
});
