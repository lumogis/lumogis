// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Session end enqueue — wires web chat threads into the batch summarization pipeline.

import type { ChatMessageDTO } from "./chat";
import type { ApiClient } from "./client";

export interface SessionEndPayload {
  session_id: string;
  messages: ChatMessageDTO[];
}

/** Fire-and-forget session end; logs failures via caller. */
export function postSessionEnd(client: ApiClient, payload: SessionEndPayload): void {
  void client
    .postJson<SessionEndPayload, { status: string; session_id: string }>("/session/end", payload)
    .catch(() => {
      /* Caller may surface toast; do not block UI. */
    });
}
