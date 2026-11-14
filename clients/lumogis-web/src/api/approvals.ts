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
  return client.postJson<ElevateRequest, ElevateResponse>("/api/v1/approvals/elevate", {
    connector,
    action_type,
  });
}

// ── Proposals (LUM-623 producer + LUM-650/652 send surface) ────────────────

export interface ProposalCreateRequest {
  action_name: string;
  payload: Record<string, unknown>;
}

export interface ProposalCreateResponse {
  proposal_id: number;
  status: string;
  /** Display-only first-contact hint; the executor gate is the security truth. */
  first_contact: boolean;
}

export interface ProposalApproveResponse {
  proposal_id: number;
  status: string;
}

export interface ProposalExecuteResponse {
  success: boolean;
  output: string;
  error: string | null;
  reverse_token: string | null;
  /** Machine failure token (e.g. "no_drafts_folder") — branch on this, not `error`. */
  code: string | null;
}

export interface PendingProposalItem {
  proposal_id: number;
  action_name: string;
  payload: Record<string, unknown>;
  status: string;
  created_at: string | null;
  first_contact: boolean;
}

export interface PendingProposalsResponse {
  proposals: PendingProposalItem[];
}

export async function createProposal(
  client: ApiClient,
  action_name: string,
  payload: Record<string, unknown>,
): Promise<ProposalCreateResponse> {
  return client.postJson<ProposalCreateRequest, ProposalCreateResponse>(
    "/api/v1/approvals/proposals",
    { action_name, payload },
  );
}

export async function approveProposal(
  client: ApiClient,
  proposalId: number,
): Promise<ProposalApproveResponse> {
  return client.postJson<Record<string, never>, ProposalApproveResponse>(
    `/api/v1/approvals/proposals/${proposalId}/approve`,
    {},
  );
}

/** Abandon an unsent proposal (pending → rejected). LUM-682: the composer calls
 * this when the user diverts to Save-to-Drafts or closes the confirm card, so an
 * abandoned send proposal doesn't linger. Best-effort — a 404 (already gone/not
 * pending) is not an error the caller needs to surface. */
export async function cancelProposal(
  client: ApiClient,
  proposalId: number,
): Promise<ProposalApproveResponse> {
  return client.postJson<Record<string, never>, ProposalApproveResponse>(
    `/api/v1/approvals/proposals/${proposalId}/cancel`,
    {},
  );
}

export async function executeProposal(
  client: ApiClient,
  proposalId: number,
): Promise<ProposalExecuteResponse> {
  return client.postJson<Record<string, never>, ProposalExecuteResponse>(
    `/api/v1/approvals/proposals/${proposalId}/execute`,
    {},
  );
}

export async function listPendingProposals(
  client: ApiClient,
  signal?: AbortSignal,
): Promise<PendingProposalsResponse> {
  return client.getJson<PendingProposalsResponse>("/api/v1/approvals/proposals", {
    signal,
  });
}

// ── Failed-send recovery (LUM-793) ────────────────────────────────────────
// A send that fails is terminal (ADR-201) and the message lives on in
// `action_proposals.payload`, but `list_pending` filters `status = 'pending'`
// so nothing showed it. These three calls are the recovery surface.

/** What the server could establish about a failed send's actual delivery.
 *
 * `partially_sent` is NOT a nicety: `_finalize_send` logs the recipients that
 * were ACCEPTED and then fails the action on the ones that were refused, so a
 * send-log row exists for a message that only partly went out. Treating that as
 * `sent` would hide recovery from exactly the recipients who never got it. */
export type SendOutcomeState = "sent" | "partially_sent" | "not_sent" | "unknown";

export interface RecoverableAttachments {
  count: number;
  available: boolean;
  /** End of the staging grace period; after this the files are gone. */
  expires_at: string | null;
}

export interface RecoverableSendItem {
  proposal_id: number;
  action_name: string;
  to: string[];
  subject: string;
  body: string;
  failed_at: string | null;
  error: string | null;
  outcome: SendOutcomeState;
  sent_at: string | null;
  delivered_to: string[];
  refused: string[];
  attachments: RecoverableAttachments;
}

export interface RecoverableSendsResponse {
  items: RecoverableSendItem[];
}

export interface RecoverySaveDraftResponse {
  proposal_id: number;
  success: boolean;
  error: string | null;
  code: string | null;
  attachments_dropped: number;
}

export interface RecoveryDismissResponse {
  proposal_id: number;
  status: string;
}

export async function listRecoverableSends(
  client: ApiClient,
  signal?: AbortSignal,
): Promise<RecoverableSendsResponse> {
  return client.getJson<RecoverableSendsResponse>("/api/v1/approvals/recoverable-sends", {
    signal,
  });
}

export async function saveRecoverableSendToDrafts(
  client: ApiClient,
  proposalId: number,
): Promise<RecoverySaveDraftResponse> {
  return client.postJson<Record<string, never>, RecoverySaveDraftResponse>(
    `/api/v1/approvals/recoverable-sends/${proposalId}/save-draft`,
    {},
  );
}

export async function dismissRecoverableSend(
  client: ApiClient,
  proposalId: number,
): Promise<RecoveryDismissResponse> {
  return client.postJson<Record<string, never>, RecoveryDismissResponse>(
    `/api/v1/approvals/recoverable-sends/${proposalId}/dismiss`,
    {},
  );
}
