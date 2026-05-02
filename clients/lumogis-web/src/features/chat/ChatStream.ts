// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Streaming chat-completion parser (parent plan §"Phase 1 Pass 1.2 item 7").
//
// Consumes an HTTP response body that follows the OpenAI SSE chat-chunk wire
// format (`routes/chat.py::stream_completion`) and turns it into a sequence of
// callback-driven events the React UI can render incrementally:
//
//  * `onDelta(text)`           — every non-empty `delta.content` substring
//  * `onFinish(reason)`        — when `choices[0].finish_reason` is non-null
//  * `onError(message)`        — JSON parse failures, abort, transport errors
//  * `onDone()`                — terminal `[DONE]` sentinel
//
// Reuses `consumeEvents` from `src/api/sse.ts` so the W3C SSE protocol parsing
// (CRLF tolerance, comment lines, multi-line `data:`, etc.) is shared with the
// approvals SSE client (parent plan Pass 1.4) and any future SSE consumer.
//
// Why a callback model rather than an async-iterator: streaming chat needs to
// fan deltas to React state synchronously inside `useReducer` so the assistant
// bubble grows in real time. Async iterators would force every delta through
// an extra microtask and break the visual flow.
//
// The parser deliberately does NOT call `client.fetch()` itself — the caller
// (`ChatPage`) owns the AbortController and the request lifecycle so it can
// be cancelled with the user's "Stop" button without the parser holding state.

import { consumeEvents } from "../../api/sse";
import type { ChatCompletionChunk } from "../../api/chat";

export type ChatFinishReason = "stop" | "length";

export interface ChatStreamHandlers {
  onDelta(text: string): void;
  onFinish?(reason: ChatFinishReason): void;
  onError?(message: string): void;
  onDone?(): void;
}

/**
 * Drive a chat-completion fetch response through the parser. Returns when the
 * stream ends (server closed, `[DONE]` seen, or the caller aborted via the
 * AbortSignal that produced this response).
 *
 * Caller contract:
 *
 *  * `body` MUST be a `ReadableStream<Uint8Array>` (i.e. `Response.body`); we
 *    refuse to consume `null` because that means the server returned an empty
 *    body, which is itself an error condition.
 *  * Aborting the original fetch (so `getReader().read()` rejects with
 *    `AbortError`) ends the stream with a single `onDone()` call and NO
 *    `onError` — abort is a user action, not a failure.
 */
export async function consumeChatStream(
  body: ReadableStream<Uint8Array> | null,
  handlers: ChatStreamHandlers,
): Promise<void> {
  if (body === null) {
    handlers.onError?.("empty_response_body");
    handlers.onDone?.();
    return;
  }

  const reader = body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";
  let stopped = false;

  const handleData = (data: string): void => {
    if (stopped) return;
    if (data === "[DONE]") {
      stopped = true;
      handlers.onDone?.();
      return;
    }
    let chunk: ChatCompletionChunk;
    try {
      chunk = JSON.parse(data) as ChatCompletionChunk;
    } catch {
      handlers.onError?.("malformed_chunk");
      return;
    }
    const choice = chunk.choices?.[0];
    if (choice === undefined) return;
    const text = choice.delta?.content;
    if (typeof text === "string" && text.length > 0) {
      handlers.onDelta(text);
    }
    const finish = choice.finish_reason;
    if (finish === "stop" || finish === "length") {
      handlers.onFinish?.(finish);
    }
  };

  try {
    while (!stopped) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      buffer = consumeEvents(buffer, (msg) => handleData(msg.data));
    }
    if (!stopped) handlers.onDone?.();
  } catch (err) {
    if (isAbort(err)) {
      handlers.onDone?.();
      return;
    }
    handlers.onError?.(toMessage(err));
    handlers.onDone?.();
  }
}

function isAbort(err: unknown): boolean {
  if (err === null || typeof err !== "object") return false;
  const name = (err as { name?: unknown }).name;
  return name === "AbortError";
}

function toMessage(err: unknown): string {
  if (err instanceof Error) return err.message;
  if (typeof err === "string") return err;
  try {
    return JSON.stringify(err);
  } catch {
    return "unknown_stream_error";
  }
}
