// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// SSE invalidation for document library list (LUM-160).

import { useEffect, useRef } from "react";
import { useQueryClient } from "@tanstack/react-query";

import { openReconnectingSse } from "../../api/sse";
import type { AccessTokenStore } from "../../api/tokens";
import { documentsQueryKey } from "./useDocuments";
import { ingestJobQueryKey } from "./useIngestJobProgress";

// `enabled` (default true): chat opens this stream only while the library is empty (LUM-853),
// because on plain HTTP/1.1 each always-open stream takes one of the browser's six per-origin
// connections (a first-run user has two long-lived streams on chat; the fix for several
// always-open streams is one shared events consumer). LUM-853: after sign-in is turned on under
// an open tab, reconnects get 401 until the tab signs out.
export function useDocumentsSseInvalidation(
  tokens: AccessTokenStore,
  options: { enabled?: boolean } = {},
): void {
  const enabled = options.enabled ?? true;
  const qc = useQueryClient();
  const qcRef = useRef(qc);
  useEffect(() => {
    qcRef.current = qc;
  });

  useEffect(() => {
    if (!enabled) return;
    const handle = openReconnectingSse({
      url: "/api/v1/events",
      tokens,
      onMessage(msg) {
        if (msg.event === "document_status_changed") {
          void qcRef.current.invalidateQueries({ queryKey: documentsQueryKey });
        }
        if (msg.event === "ingest_progress") {
          void qcRef.current.invalidateQueries({ queryKey: documentsQueryKey });
          try {
            const payload = JSON.parse(msg.data) as { job_id?: number };
            if (typeof payload.job_id === "number") {
              void qcRef.current.invalidateQueries({
                queryKey: ingestJobQueryKey(payload.job_id),
              });
            }
          } catch {
            /* ignore malformed SSE payload */
          }
        }
      },
    });
    return () => handle.close();
  }, [tokens, enabled]);
}
