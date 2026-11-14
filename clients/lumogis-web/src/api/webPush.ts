// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Authenticated REST calls for Web Push (Phase 4C). Mirrors OpenAPI façade; no secrets logged.

import { ApiClient, ApiError } from "./client";

export interface VapidPublicKeyResponse {
  public_key: string;
}

export interface WebPushKeys {
  p256dh: string;
  auth: string;
}

/** Outgoing POST /subscribe shape (omit prefs to use server defaults / COALESCE on resubscribe). */
export interface WebPushSubscribePayload {
  endpoint: string;
  keys: WebPushKeys;
  user_agent?: string | null;
  notify_on_signals?: boolean;
  notify_on_shared_scope?: boolean;
}

export interface WebPushSubscriptionCreated {
  id: number;
  already_existed: boolean;
}

export interface WebPushSubscriptionRedacted {
  id: number;
  endpoint_origin: string;
  created_at: string;
  last_seen_at: string;
  last_error: string | null;
  user_agent: string | null;
  notify_on_signals: boolean;
  notify_on_shared_scope: boolean;
}

export interface WebPushSubscriptionsListResponse {
  subscriptions: WebPushSubscriptionRedacted[];
}

export interface WebPushSubscriptionPrefsPatch {
  notify_on_signals?: boolean;
  notify_on_shared_scope?: boolean;
}

export function getVapidPublicKey(client: ApiClient): Promise<VapidPublicKeyResponse> {
  return client.getJson<VapidPublicKeyResponse>("/api/v1/notifications/vapid-public-key");
}

export function listWebPushSubscriptions(
  client: ApiClient,
): Promise<WebPushSubscriptionsListResponse> {
  return client.getJson<WebPushSubscriptionsListResponse>("/api/v1/notifications/subscriptions");
}

export function subscribeWebPush(client: ApiClient, body: WebPushSubscribePayload): Promise<WebPushSubscriptionCreated> {
  return client.postJson<WebPushSubscribePayload, WebPushSubscriptionCreated>(
    "/api/v1/notifications/subscribe",
    body,
  );
}

/**
 * Delete this member's server row for a browser subscription (sign-out, LUM-886).
 *
 * Re-POSTs the subscription to the idempotent `/subscribe` to learn the row id, then
 * DELETEs it. Uses `fetchOnce` deliberately: no refresh and no `onAuthExpired` while
 * logout is running. A 401 on the re-POST is reported as `"unauthorized"` so the caller can
 * refresh once and retry. The body omits prefs and `user_agent` — the row is deleted at once.
 */
export async function deleteOwnWebPushRowForSubscription(
  client: ApiClient,
  sub: PushSubscription,
  signal?: AbortSignal,
): Promise<"deleted" | "skipped" | "unauthorized"> {
  const json = sub.toJSON();
  const p256dh = json.keys?.p256dh;
  const auth = json.keys?.auth;
  if (!json.endpoint || !p256dh || !auth) return "skipped";
  const payload: WebPushSubscribePayload = { endpoint: json.endpoint, keys: { p256dh, auth } };
  const created = await client.fetchOnce("/api/v1/notifications/subscribe", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    signal,
  });
  if (created.status === 401) return "unauthorized";
  if (!created.ok) return "skipped";
  const row = (await created.json()) as Partial<WebPushSubscriptionCreated>;
  if (typeof row.id !== "number") return "skipped";
  const del = await client.fetchOnce(`/api/v1/notifications/subscriptions/${row.id}`, {
    method: "DELETE",
    signal,
  });
  return del.status === 204 ? "deleted" : "skipped";
}

export function patchWebPushSubscription(
  client: ApiClient,
  subscriptionId: number,
  body: WebPushSubscriptionPrefsPatch,
): Promise<WebPushSubscriptionRedacted> {
  return client.patchJson<WebPushSubscriptionPrefsPatch, WebPushSubscriptionRedacted>(
    `/api/v1/notifications/subscriptions/${subscriptionId}`,
    body,
  );
}

export function deleteWebPushSubscription(client: ApiClient, subscriptionId: number): Promise<void> {
  return client.delete<void>(`/api/v1/notifications/subscriptions/${subscriptionId}`);
}

/** Map API errors for user-visible copy — never echoes secret material. */
export function formatWebPushUserMessage(status: number, detail: string): string {
  if (status === 503 && /webpush_not_configured/i.test(detail)) {
    return "Web Push is not configured on this Lumogis server.";
  }
  if (status === 401) return "Your session expired. Sign in again to manage browser push.";
  if (detail.length > 260) return "Request failed.";
  return detail;
}

export function describeApiError(error: unknown): string {
  if (error instanceof ApiError) return formatWebPushUserMessage(error.status, error.detail);
  if (error instanceof Error && error.message) return error.message.length > 300 ? "Something went wrong." : error.message;
  return "Something went wrong.";
}
