// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Vitest unit — reconnecting SSE with Last-Event-ID.
// Parent plan §"Phase 1 Pass 1.1" + §Test cases line 1121
// ("Vitest unit: SSE reconnection with Last-Event-ID").

import { describe, expect, it, vi } from "vitest";

import { consumeEvents, openReconnectingSse, parseEvent } from "../../src/api/sse";
import { AccessTokenStore } from "../../src/api/tokens";

function streamFrom(chunks: string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  return new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
      controller.close();
    },
  });
}

/**
 * A stream that emits the given chunks, then stays open forever (until
 * `handle.close()` aborts the underlying fetch). Used to stop the SSE
 * client from reconnecting in a loop during tests that only care about
 * the first N connection attempts.
 */
function streamFromHanging(chunks: string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  return new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
      // Intentionally do NOT call controller.close().
    },
    cancel() {
      /* no-op — abort signal will tear us down. */
    },
  });
}

function sseResponse(stream: ReadableStream<Uint8Array>): Response {
  return new Response(stream, {
    status: 200,
    headers: { "Content-Type": "text/event-stream" },
  });
}

describe("parseEvent", () => {
  it("returns null for an empty block", () => {
    expect(parseEvent("")).toBeNull();
  });

  it("returns null for a comment-only block (no data: lines)", () => {
    expect(parseEvent(": ping\n: still here")).toBeNull();
  });

  it("parses a simple data-only event with the default 'message' name", () => {
    expect(parseEvent("data: hello")).toEqual({
      id: null,
      event: "message",
      data: "hello",
    });
  });

  it("joins multiple data: lines with newlines", () => {
    expect(parseEvent("data: line1\ndata: line2")).toEqual({
      id: null,
      event: "message",
      data: "line1\nline2",
    });
  });

  it("respects custom event: + id:", () => {
    expect(parseEvent("event: action_executed\nid: 42\ndata: payload")).toEqual({
      id: "42",
      event: "action_executed",
      data: "payload",
    });
  });

  it("trims a single leading space from values per the SSE spec", () => {
    expect(parseEvent("data:   spaced")).toEqual({
      id: null,
      event: "message",
      data: "  spaced",
    });
  });
});

describe("consumeEvents", () => {
  it("emits complete events and returns the leftover", () => {
    const out: unknown[] = [];
    const leftover = consumeEvents("data: a\n\ndata: b\n\ndata: c", (e) => out.push(e));
    expect(out).toEqual([
      { id: null, event: "message", data: "a" },
      { id: null, event: "message", data: "b" },
    ]);
    expect(leftover).toBe("data: c");
  });

  it("normalises CRLF/CR line endings", () => {
    const out: unknown[] = [];
    const leftover = consumeEvents("data: a\r\n\r\ndata: b\r\n\r\n", (e) => out.push(e));
    expect(out).toEqual([
      { id: null, event: "message", data: "a" },
      { id: null, event: "message", data: "b" },
    ]);
    expect(leftover).toBe("");
  });
});

describe("openReconnectingSse", () => {
  it("calls onMessage for each event in the first connect", async () => {
    const tokens = new AccessTokenStore();
    tokens.set("tok");
    const fetchImpl = vi.fn(async () =>
      sseResponse(streamFrom(["data: a\n\nid: 1\ndata: b\n\n"])),
    );
    const messages: { id: string | null; data: string }[] = [];
    const handle = openReconnectingSse({
      url: "/events",
      tokens,
      fetchImpl: fetchImpl as unknown as typeof fetch,
      onMessage: (m) => messages.push({ id: m.id, data: m.data }),
    });

    await new Promise((r) => setTimeout(r, 20));

    expect(messages).toEqual([
      { id: null, data: "a" },
      { id: "1", data: "b" },
    ]);

    handle.close();
  });

  it("sends Authorization: Bearer + Last-Event-ID on reconnect", async () => {
    const tokens = new AccessTokenStore();
    tokens.set("tok");
    let connectCount = 0;
    const fetchCalls: Array<{ headers: Record<string, string> }> = [];
    const fetchImpl = vi.fn(async (_input: RequestInfo, init?: RequestInit) => {
      connectCount += 1;
      const headers = Object.fromEntries(new Headers(init!.headers).entries());
      fetchCalls.push({ headers });
      if (connectCount === 1) {
        return sseResponse(streamFrom(["id: 7\ndata: first\n\n"]));
      }
      // Second + later calls: emit then hang so the reconnect loop stops here.
      return sseResponse(streamFromHanging(["id: 8\ndata: second\n\n"]));
    });

    const messages: string[] = [];
    const handle = openReconnectingSse({
      url: "/events",
      tokens,
      onMessage: (m) => messages.push(m.data),
      fetchImpl: fetchImpl as unknown as typeof fetch,
      initialBackoffMs: 1,
      jitterMs: 0,
    });

    await new Promise((r) => setTimeout(r, 60));

    expect(messages).toEqual(["first", "second"]);
    expect(fetchCalls).toHaveLength(2);
    expect(fetchCalls[0]!.headers["authorization"]).toBe("Bearer tok");
    expect(fetchCalls[0]!.headers["last-event-id"]).toBeUndefined();
    expect(fetchCalls[1]!.headers["last-event-id"]).toBe("7");

    handle.close();
  });

  it("close() prevents further onMessage callbacks after abort", async () => {
    const tokens = new AccessTokenStore();
    let resolveBody!: () => void;
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(new TextEncoder().encode("data: a\n\n"));
        // Hold the stream open until close() runs.
        const wait = new Promise<void>((r) => {
          resolveBody = r;
        });
        void wait.then(() => controller.close());
      },
    });
    const fetchImpl = vi.fn(async () => sseResponse(stream));
    const messages: string[] = [];
    const handle = openReconnectingSse({
      url: "/events",
      tokens,
      fetchImpl: fetchImpl as unknown as typeof fetch,
      onMessage: (m) => messages.push(m.data),
    });

    await new Promise((r) => setTimeout(r, 10));
    expect(messages).toEqual(["a"]);

    handle.close();
    expect(handle.closed).toBe(true);
    resolveBody();
    await new Promise((r) => setTimeout(r, 10));
    // No additional onMessage calls after close().
    expect(messages).toEqual(["a"]);
  });

  it("schedules a reconnect on a non-2xx response", async () => {
    const tokens = new AccessTokenStore();
    let connectCount = 0;
    const fetchImpl = vi.fn(async () => {
      connectCount += 1;
      if (connectCount === 1) {
        return new Response("server error", { status: 500 });
      }
      // Hang after emitting "ok" so we don't trigger a third reconnect.
      return sseResponse(streamFromHanging(["data: ok\n\n"]));
    });
    const errors: unknown[] = [];
    const messages: string[] = [];
    const handle = openReconnectingSse({
      url: "/events",
      tokens,
      onMessage: (m) => messages.push(m.data),
      onError: (e) => errors.push(e),
      fetchImpl: fetchImpl as unknown as typeof fetch,
      initialBackoffMs: 1,
      jitterMs: 0,
    });

    await new Promise((r) => setTimeout(r, 50));

    expect(errors).toHaveLength(1);
    expect(messages).toEqual(["ok"]);
    handle.close();
  });
});
