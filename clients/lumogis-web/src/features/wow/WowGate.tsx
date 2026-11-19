// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Renders guided first-query and entity-discovery cards on the chat surface (LUM-216).

import { useCallback, type JSX } from "react";

import { describeApiError } from "../../api/webPush";
import { useAuth } from "../../auth/AuthProvider";
import { buildAskAboutQuery } from "./askAboutEntity";
import { EntityDiscoveryCard } from "./EntityDiscoveryCard";
import { GuidedFirstQueryCard } from "./GuidedFirstQueryCard";
import { useWowReadinessSseInvalidation } from "./useWowReadinessSse";
import { useWowState } from "./useWowState";
import { Alert, AlertDescription } from "../../components/ui/alert";
import { Button } from "../../components/ui/button";

export interface WowGateProps {
  onPrefillComposer: (text: string, options?: { wowDismissOnSend?: boolean }) => void;
}

export function WowGate({ onPrefillComposer }: WowGateProps): JSX.Element | null {
  const { client, tokens } = useAuth();
  const { query, dismissWow, dismissError, isDismissing } = useWowState(client);
  useWowReadinessSseInvalidation(tokens, query.data?.entities_ready);

  const handleDismiss = useCallback(async () => {
    await dismissWow();
  }, [dismissWow]);

  const handlePickPrompt = useCallback(
    (prompt: string) => {
      onPrefillComposer(prompt, { wowDismissOnSend: true });
    },
    [onPrefillComposer],
  );

  const handleTypeOwn = useCallback(() => {
    onPrefillComposer("", { wowDismissOnSend: false });
  }, [onPrefillComposer]);

  const handleAskAbout = useCallback(
    (name: string) => {
      onPrefillComposer(buildAskAboutQuery(name), { wowDismissOnSend: true });
    },
    [onPrefillComposer],
  );

  if (query.status === "pending") {
    return null;
  }

  if (query.status === "error") {
    return (
      <Alert variant="destructive" data-testid="wow-gate-error">
        <AlertDescription>
          Could not load suggestions: {describeApiError(query.error)}.{" "}
          <Button variant="link" size="xs" className="h-auto p-0" onClick={() => void query.refetch()}>
            Retry
          </Button>
        </AlertDescription>
      </Alert>
    );
  }

  const data = query.data;
  if (data === undefined) {
    return null;
  }

  const onboardingDone =
    data.onboarding_completed_at != null && data.onboarding_completed_at !== "";
  const notDismissed = data.wow_dismissed_at == null || data.wow_dismissed_at === "";
  const showCards = onboardingDone && data.entities_ready && notDismissed;

  if (!showCards) {
    return null;
  }

  return (
    <div className="flex flex-col gap-4" data-testid="wow-gate">
      {dismissError !== null && (
        <p role="alert" className="m-0 text-xs text-destructive">
          {dismissError}
        </p>
      )}
      <GuidedFirstQueryCard
        onPickPrompt={handlePickPrompt}
        onTypeOwn={handleTypeOwn}
        onDismiss={handleDismiss}
        isDismissing={isDismissing}
      />
      {data.top_entities.length > 0 && (
        <EntityDiscoveryCard
          entities={data.top_entities}
          onAskAbout={handleAskAbout}
          onDismiss={handleDismiss}
          isDismissing={isDismissing}
        />
      )}
    </div>
  );
}
