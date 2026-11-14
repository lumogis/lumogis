// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-648 — live email lane refresh. Subscribes to the /events SSE stream and,
// on a `needs_you` / `morning_brief` event (emitted by the in-app channel when the
// snooze-scan re-surfaces an email or the morning brief fires), invalidates the
// two email query caches so the lanes update without a manual refresh.
//
// Page-scoped by design: mounted in `EmailInboxView`, it only live-refreshes while
// the user is on `/email` (each SSE feature opens its own scoped connection via
// `openReconnectingSse`). It is NOT the "reach you elsewhere" mechanism — that is
// web push / ntfy plus the next `/email` visit.

import { useEffect, useRef } from "react";
import { useQueryClient } from "@tanstack/react-query";

import { openReconnectingSse } from "../../api/sse";
import type { AccessTokenStore } from "../../api/tokens";

/** Recency-window email fetch. Shared with EmailInboxView so both stay in sync. */
export const EMAIL_WINDOWED_KEY = ["email", "signals", "imap"] as const;
/** Recency-independent triaged-set fetch. */
export const EMAIL_TRIAGED_KEY = ["email", "signals", "imap", "triaged"] as const;

const LIVE_EVENTS = new Set(["needs_you", "morning_brief"]);

export function useEmailLiveUpdates(tokens: AccessTokenStore): void {
  const qc = useQueryClient();
  const qcRef = useRef(qc);
  useEffect(() => {
    qcRef.current = qc;
  });

  useEffect(() => {
    const handle = openReconnectingSse({
      url: "/api/v1/events",
      tokens,
      onMessage(msg) {
        if (LIVE_EVENTS.has(msg.event)) {
          void qcRef.current.invalidateQueries({ queryKey: EMAIL_WINDOWED_KEY });
          void qcRef.current.invalidateQueries({ queryKey: EMAIL_TRIAGED_KEY });
        }
      },
    });
    return () => handle.close();
  }, [tokens]);
}
