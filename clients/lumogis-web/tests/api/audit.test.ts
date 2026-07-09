// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis

import { describe, expect, it } from "vitest";

import type { AuditEntry } from "../../src/api/audit";
import { buildAuditStreamUrl, mergeAuditRows } from "../../src/api/audit";

describe("audit api helpers", () => {
  it("buildAuditStreamUrl carries filters and since_id", () => {
    const url = buildAuditStreamUrl({
      sinceId: 42,
      eventType: "action.executed",
      after: "2026-01-01T00:00:00Z",
      connector: "smtp",
      asUser: "alice",
    });
    expect(url).toContain("/api/v1/audit/stream?");
    expect(url).toContain("since_id=42");
    expect(url).toContain("event_type=action.executed");
    expect(url).toContain("connector=smtp");
    expect(url).toContain("as_user=alice");
  });

  it("mergeAuditRows dedupes by id and sorts newest first", () => {
    const base: AuditEntry[] = [
      {
        id: 2,
        action_name: "b",
        connector: "c",
        mode: "ASK",
        input_summary: null,
        result_summary: null,
        reverse_token: null,
        reverse_action: null,
        executed_at: null,
        reversed_at: null,
        event_type: "action.executed",
        scope: "personal",
        source: null,
        description: null,
      },
    ];
    const live: AuditEntry[] = [
      { ...base[0] },
      {
        id: 3,
        action_name: "new",
        connector: "c",
        mode: "ASK",
        input_summary: null,
        result_summary: null,
        reverse_token: null,
        reverse_action: null,
        executed_at: null,
        reversed_at: null,
        event_type: "action.executed",
        scope: "personal",
        source: null,
        description: null,
      },
    ];
    const merged = mergeAuditRows(base, live);
    expect(merged.map((r) => r.id)).toEqual([3, 2]);
  });
});
