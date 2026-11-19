// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Household "Online or local AI" setting, rebuilt on the shared components in
// LUM-993 plan B Pass 6: a PageSection with a radio group for the policy, a
// checkbox for the lock, and the save as a toast.

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";

import {
  type AdminSettingsPrivacy,
  type InstancePrivacyMode,
  putAdminPrivacySettings,
} from "../../api/privacyMode";
import { ApiError } from "../../api/client";
import { useAuth } from "../../auth/AuthProvider";
import { PageSection, Row } from "../../components/page/PageSection";
import { Alert, AlertDescription } from "../../components/ui/alert";
import { Button } from "../../components/ui/button";
import { Checkbox } from "../../components/ui/checkbox";
import { Label } from "../../components/ui/label";
import { RadioGroup, RadioGroupItem } from "../../components/ui/radio-group";
import { privacyWords } from "./privacyWords";

function errMsg(e: unknown): string {
  if (e instanceof ApiError) return e.detail;
  if (e instanceof Error) return e.message;
  return "Request failed";
}

interface Props {
  initial: AdminSettingsPrivacy;
}

export function PrivacyModePanel({ initial }: Props): JSX.Element {
  const { client } = useAuth();
  const queryClient = useQueryClient();
  const [mode, setMode] = useState<InstancePrivacyMode>(initial.privacy_mode);
  const [locked, setLocked] = useState(initial.privacy_mode_locked);
  const [error, setError] = useState<string | null>(null);

  const mutation = useMutation({
    mutationFn: () =>
      putAdminPrivacySettings(client, {
        privacy_mode: mode,
        privacy_mode_locked: locked,
      }),
    onSuccess: (data) => {
      setError(null);
      toast("Saved.");
      // PUT /settings returns the full freshly-persisted settings (same builder
      // as GET) — seed AdminPrivacyModeView's query directly instead of
      // invalidating, so the effective-policy readout updates without a refetch.
      queryClient.setQueryData(["admin", "settings", "privacy"], data);
      // LUM-881: the model-picker hint reads the caller's own view of the policy.
      void queryClient.invalidateQueries({ queryKey: ["me", "privacy-mode"] });
    },
    onError: (e) => setError(errMsg(e)),
  });

  return (
    <div>
      {/* No heading of its own: the Settings frame's <h1> titles the page; the
          section below is titled by what it decides. */}
      <PageSection
        title="Which AI may Lumogis use?"
        description="Decide whether Lumogis may send questions to online AI services, or keep everything on your own machine."
        info="This is a rule inside Lumogis, not a network block. With local only, online models are hidden and cannot be used; chat falls back to your local model and tells you so."
      >
        <RadioGroup
          aria-label="Household privacy mode"
          value={mode}
          onValueChange={(v) => setMode(v as InstancePrivacyMode)}
          className="gap-3"
        >
          <div className="flex items-center gap-2">
            <RadioGroupItem id="privacy-mode-local" value="local_only" />
            <Label htmlFor="privacy-mode-local" className="font-normal">
              Local only: nothing leaves your machine
            </Label>
          </div>
          <div className="flex items-center gap-2">
            <RadioGroupItem
              id="privacy-mode-cloud"
              value="allow_cloud"
              disabled={initial.privacy_mode_locked && initial.privacy_mode === "local_only"}
            />
            <Label htmlFor="privacy-mode-cloud" className="font-normal">
              Online allowed: people may use the online models they have set up
            </Label>
          </div>
        </RadioGroup>

        <div className="mt-4 flex items-start gap-2">
          <Checkbox
            id="privacy-mode-lock"
            checked={locked}
            disabled={mode === "allow_cloud"}
            onCheckedChange={(c) => setLocked(c === true)}
          />
          <Label htmlFor="privacy-mode-lock" className="font-normal">
            Lock to local only (online AI stays off until you unlock it)
          </Label>
        </div>

        <dl className="m-0 mt-4">
          <Row label="In effect now">{privacyWords(initial.privacy_effective)}</Row>
        </dl>

        {error ? (
          <Alert variant="destructive" className="mt-3">
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        ) : null}
        <div className="mt-4">
          <Button type="button" disabled={mutation.isPending} onClick={() => mutation.mutate()}>
            Save privacy settings
          </Button>
        </div>
      </PageSection>
    </div>
  );
}
