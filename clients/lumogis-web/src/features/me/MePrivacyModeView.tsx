// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Your own "Online or local AI" choice, on the shared components (LUM-993 plan B
// Pass 6): the household rule and your effective rule as facts, and one switch
// to keep your own AI local when the household would allow online.

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { fetchMePrivacyMode, patchMePrivacyMode } from "../../api/privacyMode";
import { ApiError } from "../../api/client";
import { useAuth } from "../../auth/AuthProvider";
import { ErrorState } from "../../components/page/ErrorState";
import { LoadingPlaceholder, SkeletonText } from "../../components/page/LoadingState";
import { PageSection, Row } from "../../components/page/PageSection";
import { Alert, AlertDescription } from "../../components/ui/alert";
import { Label } from "../../components/ui/label";
import { Switch } from "../../components/ui/switch";
import { privacyWords } from "../admin/privacyWords";

function errMsg(e: unknown): string {
  if (e instanceof ApiError) return e.detail;
  if (e instanceof Error) return e.message;
  return "Request failed";
}

export function MePrivacyModeView(): JSX.Element {
  const { client } = useAuth();
  const queryClient = useQueryClient();

  const q = useQuery({
    queryKey: ["me", "privacy-mode"],
    queryFn: () => fetchMePrivacyMode(client),
  });

  const mutation = useMutation({
    mutationFn: (localOnly: boolean) =>
      patchMePrivacyMode(client, {
        user_restriction: localOnly ? "local_only" : "inherit",
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["me", "privacy-mode"] });
    },
  });

  if (q.isLoading) {
    return (
      <LoadingPlaceholder label="Loading your privacy setting…" className="max-w-2xl">
        <SkeletonText lines={3} />
      </LoadingPlaceholder>
    );
  }
  if (q.isError) {
    return (
      <ErrorState
        message="Lumogis couldn't load your privacy setting."
        onRetry={() => void q.refetch()}
      />
    );
  }

  const data = q.data!;
  const furtherRestrict = data.user_restriction === "local_only";
  const canToggle =
    data.instance.privacy_effective === "allow_cloud" && !data.instance.privacy_mode_locked;

  return (
    <section>
      <PageSection
        title="Which AI may Lumogis use for you?"
        description="Your household decides whether online AI services may be used. You can keep your own AI local, but you cannot turn online AI on beyond what the household allows."
      >
        <dl className="m-0">
          <Row label="Household rule">{privacyWords(data.instance.privacy_effective)}</Row>
          <Row label="For you">{privacyWords(data.privacy_effective)}</Row>
        </dl>
        {canToggle ? (
          <div className="mt-4 flex items-start gap-3">
            <Switch
              id="me-privacy-local-only"
              checked={furtherRestrict}
              disabled={mutation.isPending}
              onCheckedChange={(c) => mutation.mutate(c)}
            />
            <Label htmlFor="me-privacy-local-only" className="font-normal">
              Keep my AI on this machine (local only)
            </Label>
          </div>
        ) : (
          <p className="m-0 mt-4 text-sm text-muted-foreground">
            {data.instance.privacy_mode_locked
              ? "Your household has locked Lumogis to local AI only."
              : "Your household uses local AI only, so online AI is not available to you."}
          </p>
        )}
        {mutation.isError ? (
          <Alert variant="destructive" className="mt-3">
            <AlertDescription>{errMsg(mutation.error)}</AlertDescription>
          </Alert>
        ) : null}
      </PageSection>
    </section>
  );
}
