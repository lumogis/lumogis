// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
// Wire shapes for the admin safety playground (LUM-141).

export interface SafetyCaseResult {
  name: string;
  vector: string;
  expected: string;
  actual: string;
  passed: boolean;
  known_gap: boolean;
  detail: string;
}

export interface SafetySuiteResult {
  total: number;
  passed: number;
  failed: number;
  warnings: number;
  ran_at: string;
  results: SafetyCaseResult[];
}

export interface SafetyCaseInfo {
  name: string;
  vector: string;
  expected: string;
  known_gap: boolean;
}

export interface SafetyCaseList {
  items: SafetyCaseInfo[];
}

export interface SafetyProbeResult {
  vector: string;
  expected: string | null;
  actual: string;
  passed: boolean | null;
  detail: string;
}

export const SAFETY_VECTORS = [
  "document_ingest",
  "session_context",
  "tool_result",
  "user_config",
  "action_execution",
] as const;

export type SafetyVector = (typeof SAFETY_VECTORS)[number];
