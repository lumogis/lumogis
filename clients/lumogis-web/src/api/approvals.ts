// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Hand-written DTOs for the approvals, audit, and elevation endpoints.
// Mirrors orchestrator/models/api_v1.py (DeniedActionItem, ElevationCandidateItem,
// PendingApprovalsResponse, ConnectorModeRequest, ElevateRequest, AuditEntryDTO)
// per parent plan §"Phase 1 Pass 1.4".

import type { ApiClient } from "./client";

// ── Risk tier ─────────────────────────────────────────────────────────────

export type RiskTier = "low" | "medium" | "high" | "hard_limit";

// ── Pending approvals ─────────────────────────────────────────────────────

export interface DeniedActionItem {
  kind: "denied_action";
  action_log_id: number;
  connector: string;
  action_type: string;
  risk_tier: RiskTier;
  input_summary: string | null;
  occurred_at: string; // ISO-8601
  elevation_eligible: boolean;
  suggested_action: "set_connector_do" | "elevate_action_type" | "explain_only";
}

export interface ElevationCandidateItem {
  kind: "elevation_candidate";
  connector: string;
  action_type: string;
  approval_count: number;
  risk_tier: RiskTier;
  elevation_eligible: boolean;
}

export type PendingApprovalItem = DeniedActionItem | ElevationCandidateItem;

export interface PendingApprovalsResponse {
  pending: PendingApprovalItem[];
}

// ── Connector mode ────────────────────────────────────────────────────────

export type ConnectorMode = "ASK" | "DO";

export interface ConnectorModeRequest {
  mode: ConnectorMode;
}

export interface ConnectorModeResponse {
  connector: string;
  mode: ConnectorMode;
}

// ── Elevation ─────────────────────────────────────────────────────────────

export interface ElevateRequest {
  connector: string;
  action_type: string;
}

export interface ElevateResponse {
  connector: string;
  action_type: string;
  elevated: true;
}

// ── Audit ─────────────────────────────────────────────────────────────────

export interface AuditEntry {
  id: number;
  action_name: string;
  connector: string;
  mode: string;
  input_summary: string | null;
  result_summary: string | null;
  reverse_token: string | null;
  reverse_action: unknown | null;
  executed_at: string | null; // ISO-8601
  reversed_at: string | null; // ISO-8601
}

export interface AuditListResponse {
  audit: AuditEntry[];
}

export interface AuditReverseResponse {
  status: "reversed";
  reverse_token: string;
}

// ── Error response ────────────────────────────────────────────────────────

export interface ErrorResponse {
  error: string;
  detail?: string;
}

/** Stable error literals from the server (plan §Error handling contract). */
export const APPROVALS_ERROR_LITERALS = {
  hard_limited_connector: "hard_limited_connector",
  hard_limited_action: "hard_limited_action",
  unknown_connector: "unknown_connector",
  unknown_action: "unknown_action",
  admin_required: "admin_required",
  unknown_reverse_token: "unknown_reverse_token",
  already_reversed: "already_reversed",
  reverse_failed: "reverse_failed",
} as const;

// ── Fetch helpers ─────────────────────────────────────────────────────────

export async function getPendingApprovals(
  client: ApiClient,
  limit = 50,
  signal?: AbortSignal,
): Promise<PendingApprovalsResponse> {
  const params = new URLSearchParams({ limit: String(limit) });
  return client.getJson<PendingApprovalsResponse>(
    `/api/v1/approvals/pending?${params.toString()}`,
    { signal },
  );
}

export async function setConnectorMode(
  client: ApiClient,
  connector: string,
  mode: ConnectorMode,
): Promise<ConnectorModeResponse> {
  return client.postJson<ConnectorModeRequest, ConnectorModeResponse>(
    `/api/v1/approvals/connector/${encodeURIComponent(connector)}/mode`,
    { mode },
  );
}

export async function elevateActionType(
  client: ApiClient,
  connector: string,
  action_type: string,
): Promise<ElevateResponse> {
  return client.postJson<ElevateRequest, ElevateResponse>(
    "/api/v1/approvals/elevate",
    { connector, action_type },
  );
}
