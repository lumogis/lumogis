// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Client for the secure address at home (LUM-331): Admin → Secure address, and
// the member-readable `GET /api/v1/secure-address` used by the shell notice.
// The server derives `state`; the words live in features/admin/secureAddressCopy.ts.

import type { ApiClient } from "./client";

export const SECURE_ADDRESS_STATES = [
  "off",
  "needs_address",
  "registering",
  "issuing",
  "ready",
  "router_blocked",
  "error",
] as const;

export type SecureAddressState = (typeof SECURE_ADDRESS_STATES)[number];

export const SECURE_ADDRESS_ERROR_CODES = [
  "worker_unreachable",
  "registration_refused",
  "registration_rate_limited",
  "clock_skew",
  "budget_exhausted",
  "lan_address_unknown",
  "caddy_volume_missing",
  "caddy_snippet_rejected",
  "caddy_reload_failed",
  "caddy_unreachable",
  "stack_control_outdated",
  "certificate_pending",
  "renewal_failing",
  "state_corrupt",
  "registration_retired",
  "caddy_image_outdated",
  "service_full",
] as const;

export type SecureAddressErrorCode = (typeof SECURE_ADDRESS_ERROR_CODES)[number];

export const SECURE_ADDRESS_WARNINGS = [
  "caddy_unreachable",
  "address_mismatch",
  "two_computers",
  "budget_exhausted",
] as const;

export type SecureAddressWarning = (typeof SECURE_ADDRESS_WARNINGS)[number];

export type LanAddressSource = "detected" | "request" | "manual";

export type DetectionUnavailableReason =
  | "docker_desktop"
  | "not_rfc1918"
  | "failed"
  | "helper_image_missing";

export interface SecureAddressStatus {
  state: SecureAddressState;
  name: string | null;
  url: string | null;
  lan_address: string | null;
  lan_address_source: LanAddressSource | null;
  suggested_address: string | null;
  detection_unavailable_reason: DetectionUnavailableReason | null;
  certificate_valid_until: string | null;
  resolver: "ok" | "blocked" | "pending" | "unknown";
  error_code: SecureAddressErrorCode | null;
  retry_after_seconds: number | null;
  name_changed: boolean;
  name_new_until: string | null;
  sign_in_enabled: boolean;
  warnings: SecureAddressWarning[];
}

export interface SecureAddressPublic {
  url: string | null;
}

const ADMIN = "/api/v1/admin/secure-address";

export function fetchSecureAddress(client: ApiClient): Promise<SecureAddressStatus> {
  return client.getJson<SecureAddressStatus>(ADMIN);
}

export function enableSecureAddress(
  client: ApiClient,
  lanAddress: string,
): Promise<SecureAddressStatus> {
  return client.postJson<{ lan_address: string }, SecureAddressStatus>(`${ADMIN}/enable`, {
    lan_address: lanAddress,
  });
}

export function setSecureAddressLan(
  client: ApiClient,
  lanAddress: string,
): Promise<SecureAddressStatus> {
  return client.putJson<{ lan_address: string }, SecureAddressStatus>(`${ADMIN}/lan-address`, {
    lan_address: lanAddress,
  });
}

export function retrySecureAddress(client: ApiClient): Promise<SecureAddressStatus> {
  return client.postJson<Record<string, never>, SecureAddressStatus>(`${ADMIN}/retry`, {});
}

export function disableSecureAddress(client: ApiClient): Promise<SecureAddressStatus> {
  return client.postJson<Record<string, never>, SecureAddressStatus>(`${ADMIN}/disable`, {});
}

export function detectSecureAddress(client: ApiClient): Promise<SecureAddressStatus> {
  return client.postJson<Record<string, never>, SecureAddressStatus>(`${ADMIN}/detect`, {});
}

export function fetchPublicSecureAddress(client: ApiClient): Promise<SecureAddressPublic> {
  return client.getJson<SecureAddressPublic>("/api/v1/secure-address");
}
