// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Client for the admin knowledge-graph endpoints: status (LUM-937) and
// Turn on / Turn off (LUM-989). The state lists below are pinned to the OpenAPI
// snapshot by tests/api/knowledgeGraphContract.test.ts. The server computes the
// state and which buttons are available (`turn_on`, `turn_off`); the Web never
// derives either.

import type { ApiClient } from "./client";

export const KNOWLEDGE_GRAPH_STATES = [
  "on",
  "starting",
  "stopping",
  "start_failed",
  "stop_failed",
  "service_unreachable",
  "service_wiring_failed",
  "unsupported_mode",
  "off",
  "off_low_memory",
] as const;

export type KnowledgeGraphState = (typeof KNOWLEDGE_GRAPH_STATES)[number];

export const GRAPH_MODE_TOKENS = ["disabled", "inprocess", "service"] as const;

export type GraphModeToken = (typeof GRAPH_MODE_TOKENS)[number];

export const TOGGLE_FAILED_CODES = ["compose_failed", "timeout", "unreachable"] as const;
export type ToggleFailedCode = (typeof TOGGLE_FAILED_CODES)[number];

export const TURN_ON_BLOCKS = [
  "checkout",
  "compose_too_old",
  "stack_control_unreachable",
  "env_unwritable",
  "already_on",
  "in_progress",
] as const;
export type TurnOnBlock = (typeof TURN_ON_BLOCKS)[number];

export const TURN_OFF_BLOCKS = [
  "checkout",
  "compose_too_old",
  "stack_control_unreachable",
  "env_unwritable",
  "already_off",
  "in_progress",
] as const;
export type TurnOffBlock = (typeof TURN_OFF_BLOCKS)[number];

export type InstallKind = "release" | "checkout" | "unknown";

export interface AdminKnowledgeGraphTurnOn {
  available: boolean;
  block: TurnOnBlock | null;
  /** The memory check says short: ask for the "I understand" confirmation first. */
  needs_confirmation: boolean;
}

export interface AdminKnowledgeGraphTurnOff {
  available: boolean;
  block: TurnOffBlock | null;
}

export interface AdminKnowledgeGraphService {
  registered: boolean;
  healthy: boolean | null;
  last_seen_healthy: string | null;
  last_unhealthy_reason: string | null;
}

export interface AdminKnowledgeGraphMemory {
  total_bytes: number | null;
  required_bytes: number;
  enough: boolean | null;
}

export interface AdminKnowledgeGraphStatus {
  state: KnowledgeGraphState;
  mode: GraphModeToken;
  configured_mode: GraphModeToken;
  service: AdminKnowledgeGraphService;
  memory: AdminKnowledgeGraphMemory;
  install_kind: InstallKind;
  turn_on: AdminKnowledgeGraphTurnOn;
  turn_off: AdminKnowledgeGraphTurnOff;
  failed_code: ToggleFailedCode | null;
}

/** Core restarts while the graph changes, so the page polls until it settles. */
export const KNOWLEDGE_GRAPH_POLL_MS = 5000;

/** The page and the System status offer line share this cached query. */
export const KNOWLEDGE_GRAPH_QUERY_KEY = ["admin", "knowledge-graph"] as const;

/** The states during which the page polls: Core restarts while it changes. */
export function isTransientState(state: KnowledgeGraphState): boolean {
  return state === "starting" || state === "stopping";
}

export async function fetchKnowledgeGraphStatus(
  client: ApiClient,
): Promise<AdminKnowledgeGraphStatus> {
  return client.getJson<AdminKnowledgeGraphStatus>("/api/v1/admin/knowledge-graph");
}

export interface KnowledgeGraphToggleResponse {
  state: "starting" | "stopping";
}

/**
 * Turn the graph on. `confirmLowMemory` is the admin's "I understand" when the
 * memory check says the machine is short. Rejects with an `ApiError` whose
 * `detail` is `needs_confirmation`, `blocked` or `busy` on a 409.
 */
export async function enableKnowledgeGraph(
  client: ApiClient,
  confirmLowMemory: boolean,
): Promise<KnowledgeGraphToggleResponse> {
  return client.postJson<{ confirm_low_memory: boolean }, KnowledgeGraphToggleResponse>(
    "/api/v1/admin/knowledge-graph/enable",
    { confirm_low_memory: confirmLowMemory },
  );
}

export async function disableKnowledgeGraph(
  client: ApiClient,
): Promise<KnowledgeGraphToggleResponse> {
  return client.postJson<Record<string, never>, KnowledgeGraphToggleResponse>(
    "/api/v1/admin/knowledge-graph/disable",
    {},
  );
}
