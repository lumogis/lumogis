// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-216 slice 2 — invalidate wow-state when ingest/entity hooks fire on /events.

import { useEffect, useRef } from "react";
import { useQueryClient } from "@tanstack/react-query";

import { openReconnectingSse } from "../../api/sse";
import type { AccessTokenStore } from "../../api/tokens";

export function useWowReadinessSseInvalidation(
  tokens: AccessTokenStore,
  entitiesReady: boolean | undefined,
): void {
  const qc = useQueryClient();
  const qcRef = useRef(qc);
  useEffect(() => {
    qcRef.current = qc;
  });

  useEffect(() => {
    if (entitiesReady === true) {
      return;
    }
    const handle = openReconnectingSse({
      url: "/api/v1/events",
      tokens,
      onMessage(msg) {
        if (msg.event === "wow_readiness_changed") {
          void qcRef.current.invalidateQueries({ queryKey: ["me", "wow-state"] });
        }
      },
    });
    return () => handle.close();
  }, [tokens, entitiesReady]);
}
