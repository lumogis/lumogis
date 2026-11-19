// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// What the navigation shell shows (LUM-993 Decision 17): one read-only call,
// GET /api/v1/me/shell. Until it answers, or if it fails, `useShellState`
// returns undefined and optional items stay hidden (Approvals, the microphone).

import { useQuery } from "@tanstack/react-query";
import { useEffect } from "react";

import { useAuth } from "../auth/AuthProvider";
import type { ApiClient } from "./client";

export interface MeShellState {
  email_connected: boolean;
  can_act: boolean;
  pending_approvals: number;
  voice_available: boolean;
  online_ai_allowed: boolean;
  knowledge_graph_on: boolean;
}

export const ME_SHELL_QUERY_KEY = ["me", "shell"] as const;

export function fetchMeShell(client: ApiClient): Promise<MeShellState> {
  return client.getJson<MeShellState>("/api/v1/me/shell");
}

const REFRESH_EVENT = "lumogis:shell-refresh";

/**
 * Ask the shell to re-read its state now, for example after the user approved
 * the last waiting item, so the Approvals count does not lag by up to a minute.
 * An event rather than a query-client call: pages that use this may render
 * without a QueryClientProvider (their tests do).
 */
export function refreshShellState(): void {
  if (typeof window !== "undefined") window.dispatchEvent(new Event(REFRESH_EVENT));
}

export function useShellState(): MeShellState | undefined {
  const { client } = useAuth();
  const q = useQuery({
    queryKey: ME_SHELL_QUERY_KEY,
    queryFn: () => fetchMeShell(client),
    staleTime: 60_000,
    refetchOnWindowFocus: true,
  });
  const { refetch } = q;
  useEffect(() => {
    const onRefresh = () => void refetch();
    window.addEventListener(REFRESH_EVENT, onRefresh);
    return () => window.removeEventListener(REFRESH_EVENT, onRefresh);
  }, [refetch]);
  return q.data;
}
