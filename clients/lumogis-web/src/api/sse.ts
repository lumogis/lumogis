// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Reconnecting SSE client (parent plan §"Phase 1 Pass 1.1 item 3").
//
// Why fetch + ReadableStream instead of `EventSource`:
//
//  * EventSource cannot send custom headers (no `Authorization: Bearer …`).
//    The orchestrator's `/events` SSE is auth-required when `AUTH_ENABLED=true`.
//  * EventSource has its own opaque reconnect logic that ignores our
//    backoff schedule and access-token rotation.
//
// Behaviour:
//
//  * Opens `GET <url>` with `Accept: text/event-stream`, `Authorization: Bearer
//    <access_token>` (snapshot at connect time), and `Last-Event-ID: <id>` on
//    reconnects (per the SSE spec — server uses this to replay missed events).
//  * Parses the wire format incrementally (CR / LF / CRLF tolerant; `data:`,
//    `event:`, `id:`, `retry:` per W3C; consecutive blank lines flush an event).
//  * On disconnect, end-of-stream, or thrown error, schedules a reconnect with
//    exponential backoff (initial → ×2 each attempt, capped at max) plus jitter.
//  * Clean shutdown via `handle.close()` aborts the in-flight fetch and clears
//    pending reconnect timers (no further `onMessage` after close).
//  * `tokens` is consulted at every connect attempt so a 401-refresh-mid-stream
//    is picked up on the next reconnect.

import type { AccessTokenStore } from "./tokens";

export interface SseMessage {
  /** Last `id:` field seen for this event (or null if not provided). */
  id: string | null;
  /** Last `event:` field seen for this event (`"message"` if not provided). */
  event: string;
  /** Concatenated `data:` lines for this event, joined by "\n". */
  data: string;
}

export interface SseHandle {
  /** Permanently close the stream. Idempotent. */
  close(): void;
  /** True between `close()` and any subsequent `open()` (which there is none). */
  readonly closed: boolean;
}

export interface SseOptions {
  url: string;
  tokens?: AccessTokenStore;
  onMessage: (event: SseMessage) => void;
  onOpen?: () => void;
  onError?: (err: unknown) => void;
  /** First reconnect delay in ms (default 500). */
  initialBackoffMs?: number;
  /** Cap on backoff in ms (default 30_000). */
  maxBackoffMs?: number;
  /** Random jitter added to each backoff in ms (default 250). */
  jitterMs?: number;
  /** Inject fetch + setTimeout for tests. */
  fetchImpl?: typeof fetch;
  setTimeoutImpl?: typeof setTimeout;
  clearTimeoutImpl?: typeof clearTimeout;
}

const DEFAULT_INITIAL_BACKOFF_MS = 500;
const DEFAULT_MAX_BACKOFF_MS = 30_000;
const DEFAULT_JITTER_MS = 250;

export function openReconnectingSse(opts: SseOptions): SseHandle {
  const fetchImpl = opts.fetchImpl ?? ((u, i) => globalThis.fetch(u as RequestInfo, i));
  const setTimeoutImpl = opts.setTimeoutImpl ?? globalThis.setTimeout;
  const clearTimeoutImpl = opts.clearTimeoutImpl ?? globalThis.clearTimeout;
  const initialBackoff = opts.initialBackoffMs ?? DEFAULT_INITIAL_BACKOFF_MS;
  const maxBackoff = opts.maxBackoffMs ?? DEFAULT_MAX_BACKOFF_MS;
  const jitter = opts.jitterMs ?? DEFAULT_JITTER_MS;

  let closed = false;
  let abortCtrl: AbortController | null = null;
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  let lastEventId: string | null = null;
  let attempt = 0;

  function nextBackoffMs(): number {
    // attempt = 0 is the first reconnect after the first connect.
    const base = Math.min(initialBackoff * Math.pow(2, attempt), maxBackoff);
    return base + Math.floor(Math.random() * jitter);
  }

  function scheduleReconnect(): void {
    if (closed) return;
    const delay = nextBackoffMs();
    attempt += 1;
    reconnectTimer = setTimeoutImpl(() => {
      reconnectTimer = null;
      void connect();
    }, delay);
  }

  async function connect(): Promise<void> {
    if (closed) return;
    abortCtrl = new AbortController();
    const headers: Record<string, string> = { Accept: "text/event-stream" };
    const token = opts.tokens?.get();
    if (token) headers.Authorization = `Bearer ${token}`;
    if (lastEventId) headers["Last-Event-ID"] = lastEventId;

    let res: Response;
    try {
      res = await fetchImpl(opts.url, {
        method: "GET",
        headers,
        credentials: "include",
        signal: abortCtrl.signal,
      });
    } catch (err) {
      if (closed) return;
      opts.onError?.(err);
      scheduleReconnect();
      return;
    }

    if (!res.ok || !res.body) {
      opts.onError?.(new Error(`SSE connect failed: HTTP ${res.status}`));
      scheduleReconnect();
      return;
    }

    // Connection accepted → reset backoff counter.
    attempt = 0;
    opts.onOpen?.();

    const reader = res.body.getReader();
    const decoder = new TextDecoder("utf-8");
    let buffer = "";

    try {
      while (!closed) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        buffer = consumeEvents(buffer, (msg) => {
          if (msg.id !== null) lastEventId = msg.id;
          opts.onMessage(msg);
        });
      }
      if (!closed) scheduleReconnect();
    } catch (err) {
      if (closed) return;
      opts.onError?.(err);
      scheduleReconnect();
    }
  }

  void connect();

  return {
    get closed() {
      return closed;
    },
    close(): void {
      if (closed) return;
      closed = true;
      if (reconnectTimer !== null) {
        clearTimeoutImpl(reconnectTimer);
        reconnectTimer = null;
      }
      if (abortCtrl) {
        try {
          abortCtrl.abort();
        } catch {
          /* ignore */
        }
      }
    },
  };
}

/**
 * Consume as many complete SSE events as the buffer holds. Returns the leftover
 * (incomplete) tail. An "event" terminates at the first blank line (`\n\n`,
 * `\r\n\r\n`, or `\r\r`).
 */
export function consumeEvents(
  buffer: string,
  emit: (event: SseMessage) => void,
): string {
  // Normalise CRLF/CR to LF so we can split on a single delimiter.
  const normalised = buffer.replace(/\r\n?/g, "\n");
  const parts = normalised.split("\n\n");
  const leftover = parts.pop() ?? "";
  for (const raw of parts) {
    const msg = parseEvent(raw);
    if (msg !== null) emit(msg);
  }
  return leftover;
}

/**
 * Parse a single SSE event block per the W3C wire format. Returns null when
 * the block holds no `data:` lines (per spec — a comment-only block is not
 * dispatched).
 */
export function parseEvent(block: string): SseMessage | null {
  if (block.length === 0) return null;
  let id: string | null = null;
  let event = "message";
  const data: string[] = [];
  let sawData = false;

  for (const rawLine of block.split("\n")) {
    if (rawLine.length === 0) continue;
    if (rawLine.startsWith(":")) continue; // comment

    const colon = rawLine.indexOf(":");
    let field: string;
    let value: string;
    if (colon === -1) {
      field = rawLine;
      value = "";
    } else {
      field = rawLine.slice(0, colon);
      value = rawLine.slice(colon + 1);
      // Per spec: trim a single leading space if present.
      if (value.startsWith(" ")) value = value.slice(1);
    }

    switch (field) {
      case "data":
        data.push(value);
        sawData = true;
        break;
      case "event":
        if (value.length > 0) event = value;
        break;
      case "id":
        // Per spec: the empty string is a valid id; embedded NUL is NOT.
        if (!value.includes("\0")) id = value;
        break;
      case "retry":
        // Server-suggested backoff. Currently ignored (we use our own schedule).
        break;
      default:
        break;
    }
  }

  if (!sawData) return null;
  return { id, event, data: data.join("\n") };
}
