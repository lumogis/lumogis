// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// React Query hook for /api/v1/me/onboarding (LUM-165).

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback } from "react";

import type { ApiClient } from "../../api/client";
import {
  getMeOnboarding,
  patchMeOnboardingComplete,
  type MeOnboardingResponse,
} from "../../api/meOnboarding";
import { INVITE_ONBOARDING_STORAGE_KEY } from "../../api/invites";
import { describeApiError } from "../../api/webPush";

export function useOnboardingStatus(client: ApiClient) {
  const qc = useQueryClient();

  const query = useQuery({
    queryKey: ["me", "onboarding"] as const,
    queryFn: () => getMeOnboarding(client),
    staleTime: 5 * 60 * 1000,
    gcTime: 30 * 60 * 1000,
  });

  const completion = useMutation({
    mutationFn: () => patchMeOnboardingComplete(client),
    retry: false,
    onSuccess: (data: MeOnboardingResponse) => {
      qc.setQueryData(["me", "onboarding"], data);
    },
  });

  const clearCompleteError = useCallback(() => {
    completion.reset();
  }, [completion]);

  const completeOnboarding = useCallback(async () => {
    await completion.mutateAsync();
    try {
      sessionStorage.removeItem(INVITE_ONBOARDING_STORAGE_KEY);
    } catch {
      /* ignore */
    }
  }, [completion]);

  return {
    query,
    completeOnboarding,
    completeError: completion.error ? describeApiError(completion.error) : null,
    clearCompleteError,
    isCompleting: completion.isPending,
  };
}
