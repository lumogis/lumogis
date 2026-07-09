// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Service-health hook (LUM-512). Polls GET /api/v1/health to drive graceful
// degradation banners. The poll is a *proactive* signal only — the actual
// request/response is the source of truth for live errors, so surfaces call
// `refresh()` on an error response to reconcile the banner immediately.

import { useCallback, useMemo } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import type { ApiClient } from "../../api/client";
import { fetchHealth, type HealthResponse, type HealthServiceState } from "../../api/health";

export const healthQueryKey = ["service-health"] as const;

// Proactive cadence: backend caches ~10s, so polling every 20s is cheap and
// still surfaces outages/recovery promptly between user actions.
const POLL_INTERVAL_MS = 20_000;

function stateOf(health: HealthResponse | undefined, id: string): HealthServiceState | undefined {
  return health?.services[id];
}

export interface ServiceHealth {
  health: HealthResponse | undefined;
  isLoading: boolean;
  /** Ollama down — chat cannot generate (hard failure). */
  isOllamaDown: boolean;
  /** Qdrant down — document/vector search unavailable (degraded, not fatal). */
  isQdrantDown: boolean;
  /** Knowledge-graph store down — entity features unavailable (degraded). */
  isGraphDown: boolean;
  /** Force a re-read of health (e.g. after an error response). */
  refresh: () => void;
}

export function useServiceHealth(client: ApiClient): ServiceHealth {
  const queryClient = useQueryClient();
  const query = useQuery({
    queryKey: healthQueryKey,
    queryFn: () => fetchHealth(client),
    refetchInterval: POLL_INTERVAL_MS,
    // Health is ambient; don't refetch on every window focus.
    refetchOnWindowFocus: false,
    staleTime: 5_000,
  });

  // Stable across polls so consuming callbacks (e.g. chat submit) don't churn.
  const refresh = useCallback(() => {
    void queryClient.invalidateQueries({ queryKey: healthQueryKey });
  }, [queryClient]);

  // The backend keys the knowledge-graph store as "graph" (services.stack_status).
  const isOllamaDown = stateOf(query.data, "ollama") === "down";
  const isQdrantDown = stateOf(query.data, "qdrant") === "down";
  const isGraphDown = stateOf(query.data, "graph") === "down";

  // react-query's structural sharing keeps query.data referentially stable when
  // a poll returns identical state, so this memo (and the banner it feeds) does
  // not churn on no-op polls — only when a service's state actually changes.
  return useMemo(
    () => ({ health: query.data, isLoading: query.isLoading, isOllamaDown, isQdrantDown, isGraphDown, refresh }),
    [query.data, query.isLoading, isOllamaDown, isQdrantDown, isGraphDown, refresh],
  );
}
