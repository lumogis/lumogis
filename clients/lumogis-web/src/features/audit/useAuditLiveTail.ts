// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-592 — reconnecting SSE live tail for audit_log rows.

import { useEffect, useRef, useState } from "react";

import type { AuditEntry } from "../../api/audit";
import { openReconnectingSse } from "../../api/sse";
import type { AccessTokenStore } from "../../api/tokens";

export function useAuditLiveTail(opts: {
  enabled: boolean;
  streamUrl: string;
  tokens: AccessTokenStore;
}): AuditEntry[] {
  const [liveRows, setLiveRows] = useState<AuditEntry[]>([]);
  const streamUrlRef = useRef(opts.streamUrl);

  useEffect(() => {
    streamUrlRef.current = opts.streamUrl;
  }, [opts.streamUrl]);

  useEffect(() => {
    if (!opts.enabled) {
      setLiveRows([]);
      return;
    }

    const handle = openReconnectingSse({
      url: streamUrlRef.current,
      tokens: opts.tokens,
      onMessage(msg) {
        if (msg.event !== "audit_entry") return;
        try {
          const entry = JSON.parse(msg.data) as AuditEntry;
          if (typeof entry.id !== "number") return;
          setLiveRows((prev) => {
            if (prev.some((r) => r.id === entry.id)) return prev;
            return [entry, ...prev];
          });
        } catch {
          /* ignore malformed payloads */
        }
      },
    });

    return () => handle.close();
  }, [opts.enabled, opts.streamUrl, opts.tokens]);

  return liveRows;
}
