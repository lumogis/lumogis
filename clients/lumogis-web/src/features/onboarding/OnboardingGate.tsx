// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis

import { useCallback, useMemo, type JSX } from "react";

import { INVITE_ONBOARDING_STORAGE_KEY, type InviteOnboardingHintStored } from "../../api/invites";
import { describeApiError } from "../../api/webPush";
import { useAuth } from "../../auth/AuthProvider";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { OnboardingModal } from "./OnboardingModal";
import { useOnboardingStatus } from "./useOnboardingStatus";

function readInviteOnboardingHint(): InviteOnboardingHintStored | null {
  try {
    const raw = sessionStorage.getItem(INVITE_ONBOARDING_STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as InviteOnboardingHintStored;
    if (typeof parsed.allows_shared !== "boolean") return null;
    return parsed;
  } catch {
    return null;
  }
}

export function OnboardingGate(): JSX.Element | null {
  const { client } = useAuth();
  const { query, completeOnboarding, completeError, clearCompleteError } = useOnboardingStatus(client);
  const inviteOnboarding = useMemo(() => readInviteOnboardingHint(), []);

  const onComplete = useCallback(async () => {
    await completeOnboarding();
  }, [completeOnboarding]);

  if (query.status === "pending") {
    return null;
  }

  if (query.status === "error") {
    return (
      <Alert variant="destructive" className="rounded-none border-x-0 border-t-0">
        <AlertDescription className="flex flex-wrap items-center justify-center gap-2">
          <span>Could not load onboarding state: {describeApiError(query.error)}.</span>
          <Button type="button" size="sm" variant="outline" onClick={() => void query.refetch()}>
            Retry
          </Button>
        </AlertDescription>
      </Alert>
    );
  }

  const completedAt = query.data?.completed_at;
  if (completedAt != null && completedAt !== "") {
    return null;
  }

  return (
    <OnboardingModal
      inviteOnboarding={inviteOnboarding}
      onComplete={onComplete}
      completeError={completeError}
      onClearCompleteError={clearCompleteError}
    />
  );
}
