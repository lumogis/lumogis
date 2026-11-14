// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Client for the LUM-697 unsubscribe surface.
// Mirrors orchestrator/routes/api_v1/email_unsubscribe.py.
//
// Note what this client does NOT do: it never posts an unsubscribe recipient.
// It sends sender addresses; the backend resolves the recipient from its own
// stored row at propose time. That keeps a compromised session from dressing an
// arbitrary address up as an unsubscribe, and means the payload the user
// approves is the payload that sends.

import type { ApiClient } from "./client";

export interface SubscriptionRow {
  source_id: string;
  from_addr: string;
  from_display: string;
  message_count: number;
  has_mailto: boolean;
  has_https: boolean;
  one_click: boolean;
  /** Followed by the USER'S BROWSER, never by the server. Render only when it
   *  passes `isSafeUnsubscribeUrl` — a second gate behind the server-side
   *  scheme allowlist, because a row stored before a parser fix would otherwise
   *  still be live (that gap is CVE-2025-68673). */
  https_uri: string;
  last_seen_at: string | null;
  unsubscribed_at: string | null;
  suppressed_as: string | null;
}

export interface SubscriptionsResponse {
  rows: SubscriptionRow[];
  truncated: boolean;
}

export interface ResolvedTarget {
  from_addr: string;
  mailto_addr: string;
  subject: string;
}

export interface ProposeResponse {
  proposal_id: number;
  targets: ResolvedTarget[];
  /** Senders with no `mailto:` form — these need the browser hand-off instead. */
  skipped: string[];
}

/**
 * One off-thread unsubscribe batch (LUM-710).
 *
 * Execution moved to the batch worker, so an approved batch is no longer
 * finished by the time the approval call returns — it drains over the following
 * minutes, one paced send at a time. `status` is the queue's:
 * `pending | running | done | dead`.
 */
export interface UnsubscribeJobSummary {
  job_id: number;
  status: string;
  total: number;
  enqueued_at: string | null;
  finished_at: string | null;
}

export interface UnsubscribeJobsResponse {
  jobs: UnsubscribeJobSummary[];
}

export function isUnsubscribeJobActive(job: UnsubscribeJobSummary): boolean {
  return job.status === "pending" || job.status === "running";
}

export interface SenderRule {
  source_id: string;
  from_addr: string;
  action: string;
  active: boolean;
  created_at: string | null;
}

/**
 * True only for a URI we are willing to turn into a clickable link.
 *
 * The server already applies a scheme allowlist at parse time. This repeats it
 * at render because the two run at different moments: a row written before a
 * parser fix, or restored from a backup, would still be served. Injecting
 * `javascript:` into this header is a published stored-XSS technique, and the
 * mitigation is exactly this check.
 */
export function isSafeUnsubscribeUrl(uri: string): boolean {
  if (!uri) return false;
  try {
    const u = new URL(uri);
    if (u.protocol !== "https:") return false;
    // Userinfo is a display spoof (https://evil@real-bank.example/…) and a
    // credential leak into browser history. The server rejects it too.
    if (u.username || u.password) return false;
    return true;
  } catch {
    return false;
  }
}

export async function fetchSubscriptions(
  client: ApiClient,
  sourceId: string,
  abort?: AbortSignal,
): Promise<SubscriptionsResponse> {
  return client.getJson<SubscriptionsResponse>(
    `/api/v1/email/subscriptions?source_id=${encodeURIComponent(sourceId)}`,
    { signal: abort },
  );
}

/** Create ONE proposal for the whole batch. Senders only — never recipients. */
export async function proposeUnsubscribe(
  client: ApiClient,
  sourceId: string,
  senders: string[],
  abort?: AbortSignal,
): Promise<ProposeResponse> {
  return client.postJson<{ source_id: string; senders: string[] }, ProposeResponse>(
    "/api/v1/email/unsubscribe/propose",
    { source_id: sourceId, senders },
    { signal: abort },
  );
}

/**
 * This user's recent unsubscribe batches, newest first.
 *
 * How the view learns a batch is still draining: the approval response carries
 * no job id (the executor's result is a human-readable string), so the client
 * asks for its own recent batches instead.
 */
export async function fetchUnsubscribeJobs(
  client: ApiClient,
  abort?: AbortSignal,
): Promise<UnsubscribeJobsResponse> {
  return client.getJson<UnsubscribeJobsResponse>("/api/v1/email/unsubscribe/jobs", {
    signal: abort,
  });
}

export async function setSenderRule(
  client: ApiClient,
  sourceId: string,
  fromAddr: string,
  abort?: AbortSignal,
): Promise<void> {
  await client.postJson<{ source_id: string; from_addr: string; action: string }, unknown>(
    "/api/v1/email/sender-rules",
    { source_id: sourceId, from_addr: fromAddr, action: "hide" },
    { signal: abort },
  );
}

export async function clearSenderRule(
  client: ApiClient,
  sourceId: string,
  fromAddr: string,
): Promise<void> {
  const qs = `source_id=${encodeURIComponent(sourceId)}&from_addr=${encodeURIComponent(fromAddr)}`;
  await client.delete(`/api/v1/email/sender-rules?${qs}`);
}
