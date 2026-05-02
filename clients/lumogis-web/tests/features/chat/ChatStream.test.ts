// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Vitest unit — chat-completion SSE parser.
// Parent plan §"Pass 1.2 item 7" + §Test cases line 1121
// ("Vitest unit: chat parser").

import { describe, expect, it, vi } from "vitest";

import { consumeChatStream } from "../../../src/features/chat/ChatStream";

function chunk(payload: object | "[DONE]"): string {
  const data = payload === "[DONE]" ? "[DONE]" : JSON.stringify(payload);
  return `data: ${data}\n\n`;
}

function streamFrom(parts: string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  return new ReadableStream<Uint8Array>({
    start(controller) {
      for (const p of parts) controller.enqueue(encoder.encode(p));
      controller.close();
    },
  });
}

function makeChunkObject(content: string, opts: { finish?: "stop" | null; role?: "assistant" } = {}): object {
  return {
    id: "chatcmpl-lumogis",
    object: "chat.completion.chunk",
    created: 1_700_000_000,
    model: "claude",
    choices: [
      {
        index: 0,
        delta: {
          ...(opts.role !== undefined ? { role: opts.role } : {}),
          ...(content !== "" ? { content } : {}),
        },
        finish_reason: opts.finish ?? null,
      },
    ],
  };
}

describe("consumeChatStream", () => {
  it("emits each non-empty content delta in order", async () => {
    const onDelta = vi.fn();
    const onDone = vi.fn();
    const onFinish = vi.fn();
    const stream = streamFrom([
      chunk(makeChunkObject("", { role: "assistant" })),
      chunk(makeChunkObject("Hello")),
      chunk(makeChunkObject(" world")),
      chunk(makeChunkObject("!", { finish: "stop" })),
      chunk("[DONE]"),
    ]);

    await consumeChatStream(stream, { onDelta, onDone, onFinish });

    expect(onDelta).toHaveBeenCalledTimes(3);
    expect(onDelta.mock.calls.map((c) => c[0])).toEqual(["Hello", " world", "!"]);
    expect(onFinish).toHaveBeenCalledWith("stop");
    expect(onDone).toHaveBeenCalledTimes(1);
  });

  it("treats [DONE] as the terminal sentinel and stops early", async () => {
    const onDelta = vi.fn();
    const onDone = vi.fn();
    const stream = streamFrom([
      chunk(makeChunkObject("first")),
      chunk("[DONE]"),
      chunk(makeChunkObject("should-be-ignored")),
    ]);

    await consumeChatStream(stream, { onDelta, onDone });

    expect(onDelta).toHaveBeenCalledTimes(1);
    expect(onDelta).toHaveBeenCalledWith("first");
    expect(onDone).toHaveBeenCalledTimes(1);
  });

  it("re-assembles a chunk that is split across two transport reads", async () => {
    const onDelta = vi.fn();
    const onDone = vi.fn();
    const full = chunk(makeChunkObject("partial"));
    const halfwayPoint = Math.floor(full.length / 2);
    const stream = streamFrom([full.slice(0, halfwayPoint), full.slice(halfwayPoint), chunk("[DONE]")]);

    await consumeChatStream(stream, { onDelta, onDone });

    expect(onDelta).toHaveBeenCalledTimes(1);
    expect(onDelta).toHaveBeenCalledWith("partial");
    expect(onDone).toHaveBeenCalledTimes(1);
  });

  it("emits multiple deltas when several events arrive in a single transport read", async () => {
    const onDelta = vi.fn();
    const onDone = vi.fn();
    const big =
      chunk(makeChunkObject("a")) + chunk(makeChunkObject("b")) + chunk(makeChunkObject("c"));
    const stream = streamFrom([big, chunk("[DONE]")]);

    await consumeChatStream(stream, { onDelta, onDone });

    expect(onDelta.mock.calls.map((c) => c[0])).toEqual(["a", "b", "c"]);
    expect(onDone).toHaveBeenCalledTimes(1);
  });

  it("ignores chunks with empty delta.content", async () => {
    const onDelta = vi.fn();
    const onDone = vi.fn();
    const stream = streamFrom([
      chunk(makeChunkObject("", { role: "assistant" })),
      chunk(makeChunkObject("hi")),
      chunk(makeChunkObject("", { finish: "stop" })),
      chunk("[DONE]"),
    ]);

    await consumeChatStream(stream, { onDelta, onDone });

    expect(onDelta).toHaveBeenCalledTimes(1);
    expect(onDelta).toHaveBeenCalledWith("hi");
    expect(onDone).toHaveBeenCalledTimes(1);
  });

  it("calls onError with malformed_chunk for non-JSON data, then keeps going", async () => {
    const onDelta = vi.fn();
    const onError = vi.fn();
    const onDone = vi.fn();
    const stream = streamFrom([
      "data: not-json\n\n",
      chunk(makeChunkObject("recovered")),
      chunk("[DONE]"),
    ]);

    await consumeChatStream(stream, { onDelta, onError, onDone });

    expect(onError).toHaveBeenCalledWith("malformed_chunk");
    expect(onDelta).toHaveBeenCalledWith("recovered");
    expect(onDone).toHaveBeenCalledTimes(1);
  });

  it("reports empty_response_body when body is null and still calls onDone", async () => {
    const onError = vi.fn();
    const onDone = vi.fn();

    await consumeChatStream(null, { onDelta: vi.fn(), onError, onDone });

    expect(onError).toHaveBeenCalledWith("empty_response_body");
    expect(onDone).toHaveBeenCalledTimes(1);
  });

  it("treats AbortError as a clean termination (no onError, single onDone)", async () => {
    const onError = vi.fn();
    const onDone = vi.fn();

    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(new TextEncoder().encode(chunk(makeChunkObject("partial"))));
      },
      pull() {
        const err = new Error("aborted");
        err.name = "AbortError";
        throw err;
      },
    });

    await consumeChatStream(stream, { onDelta: vi.fn(), onError, onDone });

    expect(onError).not.toHaveBeenCalled();
    expect(onDone).toHaveBeenCalledTimes(1);
  });

  it("surfaces a generic transport error via onError and finalises onDone", async () => {
    const onError = vi.fn();
    const onDone = vi.fn();

    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(new TextEncoder().encode(chunk(makeChunkObject("partial"))));
      },
      pull() {
        throw new Error("network reset");
      },
    });

    await consumeChatStream(stream, { onDelta: vi.fn(), onError, onDone });

    expect(onError).toHaveBeenCalledWith("network reset");
    expect(onDone).toHaveBeenCalledTimes(1);
  });

  it("stays silent when an event has no choices array", async () => {
    const onDelta = vi.fn();
    const onError = vi.fn();
    const onDone = vi.fn();
    const stream = streamFrom([
      `data: ${JSON.stringify({ id: "x", object: "chat.completion.chunk", created: 1, model: "m", choices: [] })}\n\n`,
      chunk("[DONE]"),
    ]);

    await consumeChatStream(stream, { onDelta, onError, onDone });

    expect(onDelta).not.toHaveBeenCalled();
    expect(onError).not.toHaveBeenCalled();
    expect(onDone).toHaveBeenCalledTimes(1);
  });
});
