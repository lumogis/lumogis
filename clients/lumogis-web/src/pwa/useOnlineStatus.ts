// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Phase 3E — navigator online/offline only (no network polling, no health checks).

import { useEffect, useState } from "react";
import { onlineManager } from "@tanstack/react-query";

/** Default when `navigator` is unavailable (SSR / tests): assume online so the shell renders normally. */
function readNavigatorOnLine(): boolean {
  if (typeof navigator === "undefined") return true;
  return navigator.onLine;
}

/**
 * Live browser connectivity flag from `navigator.onLine` + `online`/`offline` events.
 * Keeps `@tanstack/react-query` {@link onlineManager} aligned for conservative reconnect refetch behaviour.
 */
export function useOnlineStatus(): boolean {
  const [online, setOnline] = useState(readNavigatorOnLine);

  useEffect(() => {
    if (typeof window === "undefined" || typeof navigator === "undefined") {
      return;
    }

    const sync = (): void => {
      const next = navigator.onLine;
      setOnline(next);
      onlineManager.setOnline(next);
    };

    sync();
    window.addEventListener("online", sync);
    window.addEventListener("offline", sync);

    return () => {
      window.removeEventListener("online", sync);
      window.removeEventListener("offline", sync);
    };
  }, []);

  return online;
}
