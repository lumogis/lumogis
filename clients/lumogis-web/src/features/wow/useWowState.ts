// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// React Query hook for /api/v1/me/wow-state (LUM-216).

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback } from "react";

import type { ApiClient } from "../../api/client";
import {
  getMeWowState,
  patchMeWowDismissed,
  type MeWowStateResponse,
} from "../../api/meWow";
import { describeApiError } from "../../api/webPush";

export function useWowState(client: ApiClient) {
  const qc = useQueryClient();

  const query = useQuery({
    queryKey: ["me", "wow-state"] as const,
    queryFn: () => getMeWowState(client),
    staleTime: 30_000,
    refetchInterval: (q) => (q.state.data?.entities_ready ? false : 4000),
  });

  const dismissMutation = useMutation({
    mutationFn: () => patchMeWowDismissed(client),
    retry: false,
    onSuccess: (data: MeWowStateResponse) => {
      qc.setQueryData(["me", "wow-state"], data);
    },
  });

  const dismissWow = useCallback(async () => {
    await dismissMutation.mutateAsync();
  }, [dismissMutation]);

  return {
    query,
    dismissWow,
    dismissError: dismissMutation.error ? describeApiError(dismissMutation.error) : null,
    isDismissing: dismissMutation.isPending,
  };
}
