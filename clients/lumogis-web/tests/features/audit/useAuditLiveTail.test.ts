// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis

import { renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { AuditEntry } from "../../../src/api/audit";
import { useAuditLiveTail } from "../../../src/features/audit/useAuditLiveTail";
import { AccessTokenStore } from "../../../src/api/tokens";

const openReconnectingSse = vi.hoisted(() => vi.fn());

vi.mock("../../../src/api/sse", () => ({
  openReconnectingSse: openReconnectingSse,
}));

describe("useAuditLiveTail", () => {
  it("subscribes when enabled and accumulates audit_entry events", async () => {
    let onMessage: ((msg: { event: string; data: string }) => void) | undefined;
    openReconnectingSse.mockImplementation((opts: { onMessage: typeof onMessage }) => {
      onMessage = opts.onMessage;
      return { close: vi.fn(), closed: false };
    });

    const tokens = new AccessTokenStore();
    const entry: AuditEntry = {
      id: 7,
      action_name: "x",
      connector: "y",
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
    };

    const { result } = renderHook(() =>
      useAuditLiveTail({
        enabled: true,
        streamUrl: "/api/v1/audit/stream?since_id=0",
        tokens,
      }),
    );

    expect(openReconnectingSse).toHaveBeenCalledWith(
      expect.objectContaining({ url: "/api/v1/audit/stream?since_id=0", tokens }),
    );

    onMessage?.({ event: "audit_entry", data: JSON.stringify(entry) });

    await waitFor(() => {
      expect(result.current).toHaveLength(1);
      expect(result.current[0]?.id).toBe(7);
    });
  });
});
