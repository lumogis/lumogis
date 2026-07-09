// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis

export interface AuditEntry {
  id: number;
  action_name: string;
  connector: string;
  mode: string;
  input_summary: string | null;
  result_summary: string | null;
  reverse_token: string | null;
  reverse_action: unknown;
  executed_at: string | null;
  reversed_at: string | null;
  event_type: string;
  scope: string;
  source: string | null;
  description: string | null;
}

export interface AuditListResponse {
  audit: AuditEntry[];
  total: number;
  limit: number;
  offset: number;
}

/** Build SSE live-tail URL sharing list filters (no pagination). */
export function buildAuditStreamUrl(params: {
  sinceId?: number;
  eventType?: string;
  after?: string;
  before?: string;
  connector?: string;
  actionType?: string;
  asUser?: string;
}): string {
  const p = new URLSearchParams();
  p.set("since_id", String(params.sinceId ?? 0));
  if (params.eventType) p.set("event_type", params.eventType);
  if (params.after) p.set("after", params.after);
  if (params.before) p.set("before", params.before);
  if (params.connector?.trim()) p.set("connector", params.connector.trim());
  if (params.actionType?.trim()) p.set("action_type", params.actionType.trim());
  if (params.asUser) p.set("as_user", params.asUser);
  return `/api/v1/audit/stream?${p.toString()}`;
}

/** Merge polled rows with live SSE rows (dedupe by id, newest first). */
export function mergeAuditRows(base: AuditEntry[], live: AuditEntry[]): AuditEntry[] {
  const byId = new Map<number, AuditEntry>();
  for (const row of [...live, ...base]) byId.set(row.id, row);
  return [...byId.values()].sort((a, b) => b.id - a.id);
}
