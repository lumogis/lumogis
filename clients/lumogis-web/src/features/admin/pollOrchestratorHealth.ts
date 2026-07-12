// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Poll legacy GET /health until the orchestrator responds (restart wait UX).

import type { ApiClient } from "../../api/client";

export interface PollOrchestratorHealthResult {
  ok: boolean;
  elapsedMs: number;
}

export async function pollOrchestratorHealth(
  client: ApiClient,
  deadlineMs = 90_000,
  intervalMs = 2_000,
): Promise<PollOrchestratorHealthResult> {
  const start = Date.now();
  const deadline = start + deadlineMs;
  while (Date.now() < deadline) {
    await new Promise((resolve) => setTimeout(resolve, intervalMs));
    try {
      const res = await client.fetch("/health");
      if (res.ok) {
        return { ok: true, elapsedMs: Date.now() - start };
      }
    } catch {
      // Expected while the orchestrator container is recreating.
    }
  }
  return { ok: false, elapsedMs: Date.now() - start };
}
