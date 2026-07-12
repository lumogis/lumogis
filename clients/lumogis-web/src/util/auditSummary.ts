// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis

import type { AuditEntry } from "../api/audit";

function looksLikeJson(s: string): boolean {
  const t = s.trim();
  return (t.startsWith("{") && t.endsWith("}")) || (t.startsWith("[") && t.endsWith("]"));
}

function tryParseJson(s: string | null | undefined): unknown {
  if (!s?.trim()) return null;
  if (!looksLikeJson(s)) return null;
  try {
    return JSON.parse(s) as unknown;
  } catch {
    return null;
  }
}

function summarizeParsed(value: unknown): string | null {
  if (value === null || value === undefined) return null;
  if (typeof value === "string") return value;
  if (typeof value !== "object") return String(value);

  const o = value as Record<string, unknown>;
  if (typeof o.message === "string" && o.message.trim()) return o.message.trim();
  if (typeof o.decline_type === "string") {
    return `External call blocked (${o.decline_type.replace(/_/g, " ")})`;
  }
  if (typeof o.error === "string" && o.error.trim()) return o.error.trim();
  if (typeof o.action === "string") return o.action;
  if (typeof o.resource_type === "string" && typeof o.resource_id === "string") {
    return `${o.resource_type} ${o.resource_id}`;
  }
  if ("family_id" in o) {
    const parts: string[] = ["Household context updated"];
    if (typeof o.family_id === "string") parts.push(`(family ${o.family_id.slice(0, 8)}…)`);
    return parts.join(" ");
  }
  const keys = Object.keys(o);
  if (keys.length <= 3) {
    return keys
      .map((k) => {
        const v = o[k];
        if (typeof v === "string" && v.length > 48) return `${k}=…`;
        return `${k}=${String(v)}`;
      })
      .join(", ");
  }
  return null;
}

/** Human-readable one-line summary for audit table cells (not raw JSON dumps). */
export function auditEntrySummary(row: AuditEntry): string {
  if (row.description?.trim() && !looksLikeJson(row.description)) {
    return row.description.trim();
  }

  for (const field of [row.result_summary, row.input_summary]) {
    if (!field?.trim()) continue;
    if (!looksLikeJson(field)) return field.trim();
    const parsed = tryParseJson(field);
    const summary = summarizeParsed(parsed);
    if (summary) return summary;
  }

  if (row.action_name?.trim()) return row.action_name.replace(/_/g, " ");
  if (row.event_type?.trim()) return row.event_type.replace(/\./g, " · ");
  return "Activity recorded";
}

/** Raw payload for expandable audit detail rows. */
export function auditEntryRawPayload(row: AuditEntry): string | null {
  const parts: string[] = [];
  if (row.input_summary?.trim()) parts.push(`input: ${row.input_summary.trim()}`);
  if (row.result_summary?.trim()) parts.push(`result: ${row.result_summary.trim()}`);
  if (parts.length === 0) return null;
  return parts.join("\n");
}
