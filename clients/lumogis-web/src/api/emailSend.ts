// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Client for the LUM-652 send surface's draft-suggestion endpoint.
// Mirrors orchestrator/routes/api_v1/email_send.py.

import type { ApiClient } from "./client";

export interface ReplyDraftResponse {
  /** The suggested reply body, or "" when no local model / no body / a miss. */
  draft: string;
}

/**
 * Ask the backend for an on-box LLM-suggested reply body for a surfaced email.
 * Resolves with `""` when no draft is available (never throws for that case) —
 * the composer treats blank as "start empty". Rejects on 404 (not the caller's
 * signal) or 429 (rate limited).
 */
export async function fetchReplyDraft(
  client: ApiClient,
  signalId: string,
  signal?: AbortSignal,
): Promise<string> {
  const res = await client.postJson<{ signal_id: string }, ReplyDraftResponse>(
    "/api/v1/email/reply-draft",
    { signal_id: signalId },
    { signal },
  );
  return res.draft ?? "";
}

// LUM-684 — on-box tone/length refine of the caller's own composer body.
export interface RefineRequest {
  text: string;
  preset?: string;
  instruction?: string;
}

export interface RefineResult {
  /** The refined body, or "" on an LLM miss (treat as "try again"). */
  text: string;
  /** false when no on-box model is configured — the composer hides/disables refine. */
  available: boolean;
}

/**
 * Rewrite the composer body per a preset or a free-form instruction, via the
 * on-box model. Rejects on 422 (bad request) / 429 (rate limited); resolves with
 * `available:false` when no local model is configured.
 */
export async function refineText(
  client: ApiClient,
  req: RefineRequest,
  signal?: AbortSignal,
): Promise<RefineResult> {
  const res = await client.postJson<RefineRequest, RefineResult>("/api/v1/email/refine", req, {
    signal,
  });
  return { text: res.text ?? "", available: res.available ?? false };
}

/** LUM-698: the quoted original for a surfaced email, shown in the composer's
 * own editable field so the user can see and trim what they send.
 *
 * Returns `""` on any miss (no body, a non-IMAP signal, an IMAP hiccup) — the
 * composer then simply shows no quote and the send still works. A 404 means the
 * signal is not the caller's own.
 *
 * `abort` is the same optional AbortSignal `fetchReplyDraft` takes; it is named
 * `abort` rather than `signal` because "signal" means an email in this codebase.
 */
export interface QuoteResponse {
  quote: string;
}

export async function fetchQuote(
  client: ApiClient,
  signalId: string,
  style: "reply" | "forward" = "reply",
  abort?: AbortSignal,
): Promise<string> {
  const res = await client.postJson<{ signal_id: string; style: string }, QuoteResponse>(
    "/api/v1/email/quote",
    { signal_id: signalId, style },
    { signal: abort },
  );
  return res.quote ?? "";
}

// LUM-705 — reply-all recipients. Its own endpoint rather than an extra field on
// /quote: a different question, a different degrade (fall back to plain Reply),
// and forward mode never wants it.
export interface ReplyAllRecipients {
  to: string[];
  cc: string[];
  /** The original's headers could not be re-fetched — open as a plain Reply.
   *  Always accompanied by an empty `cc`, so ignoring this flag is still safe. */
  degraded: boolean;
  dropped_self: boolean;
  truncated: number;
  large_thread: boolean;
}

export function fetchReplyAllRecipients(
  client: ApiClient,
  signalId: string,
): Promise<ReplyAllRecipients> {
  return client.postJson("/api/v1/email/reply-all", { signal_id: signalId });
}

// LUM-810 — the message body, as extracted text. Lumogis does not render
// sender HTML, so what comes back is plain text with bidi control characters
// already marked server-side.
/** The wire contract — exactly the server's response `Literal`. */
export type EmailBodyReason =
  | "ok"
  | "no_body"
  | "mailbox_unreachable"
  | "not_found"
  | "too_large"
  | "ambiguous";

/** What the reader can be shown, which is a superset of what the wire carries.
 *
 * The route answers 200-with-a-reason for every EXPECTED failure, so an actual
 * HTTP error means something the contract does not model — and those must not
 * all be flattened into `mailbox_unreachable`, which promises a retry that a
 * 429 or a 404 cannot honour. Client-synthesised, never sent by the server. */
export type EmailBodyDisplayReason = EmailBodyReason | "rate_limited" | "unavailable";

/** One image the sender embedded IN the message (LUM-713).
 *
 * There is deliberately no part number and no fetch handle: the bytes arrive
 * with the text, already identified by the server from their own signature and
 * bounded by its caps. `<img src={data_uri}>` needs no fetch, no object URL and
 * no revoke — so there is no lifecycle for a component to own or get wrong. */
export interface EmailInlineImage {
  content_id: string;
  /** The type the server SNIFFED. The sender's declared `Content-Type` never
   *  reaches the client, so an SVG announced as a PNG is already gone. */
  mime_type: "image/png" | "image/jpeg" | "image/gif" | "image/webp";
  /** Decoded, pre-base64. */
  size_bytes: number;
  data_uri: string;
}

export interface EmailBodyResponse {
  body: string;
  /** Cut at the server's character cap — say so rather than let the reader
   *  believe a truncated message is the whole message. */
  truncated: boolean;
  /** The mailbox did not answer. Retryable, unlike every other failure here. */
  degraded: boolean;
  reason: EmailBodyReason;
  inline_images: EmailInlineImage[];
  /** A CAP dropped an image. NOT set when one was refused: a refused part was
   *  never something the reader could have seen, and reporting it as missing
   *  content would tell them a blocked attack was lost mail. */
  inline_images_truncated: boolean;
}

export function fetchEmailBody(
  client: ApiClient,
  signalId: string,
  abort?: AbortSignal,
): Promise<EmailBodyResponse> {
  return client.postJson<{ signal_id: string }, EmailBodyResponse>(
    "/api/v1/email/body",
    { signal_id: signalId },
    { signal: abort },
  );
}
