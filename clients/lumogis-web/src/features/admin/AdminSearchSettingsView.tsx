// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Admin Search & retrieval settings (LUM-159). Exposes the BGE reranker
// quality toggle with honest RAM/download copy, pending-vs-live state,
// and restart-and-wait UX on Docker Compose.
//
// Rebuilt on the shared components in LUM-993 plan B Pass 6: one plain on/off
// switch, the restart/health state as Alerts; model name and the manual restart
// instructions live in a collapsed "Details".

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronDown } from "lucide-react";
import { useEffect, useState } from "react";

import { fetchAdminStackStatus } from "../../api/adminStackStatus";
import {
  BGE_RERANKER_DOWNLOAD_ESTIMATE,
  BGE_RERANKER_MODEL,
  BGE_RERANKER_RAM_ESTIMATE,
  fetchAdminSearchSettings,
  isRerankerBackendActive,
  putAdminRerankerEnabled,
  restartStack,
} from "../../api/searchSettings";
import { ApiError } from "../../api/client";
import { useAuth } from "../../auth/AuthProvider";
import { ErrorState } from "../../components/page/ErrorState";
import { LoadingPlaceholder, SkeletonText } from "../../components/page/LoadingState";
import { PageSection, Row } from "../../components/page/PageSection";
import { Alert, AlertDescription } from "../../components/ui/alert";
import { Button } from "../../components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "../../components/ui/collapsible";
import { Label } from "../../components/ui/label";
import { Switch } from "../../components/ui/switch";
import { pollOrchestratorHealth } from "./pollOrchestratorHealth";

function errMsg(e: unknown): string {
  if (e instanceof ApiError) return e.detail;
  if (e instanceof Error) return e.message;
  return "Request failed";
}

type RestartPhase = "idle" | "polling" | "timeout" | "done";

export function AdminSearchSettingsView(): JSX.Element {
  const { client } = useAuth();
  const queryClient = useQueryClient();
  const [rerankerEnabled, setRerankerEnabled] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [restartPhase, setRestartPhase] = useState<RestartPhase>("idle");
  const [restartStatus, setRestartStatus] = useState<string | null>(null);
  const [savedNeedsManualRestart, setSavedNeedsManualRestart] = useState(false);

  const q = useQuery({
    queryKey: ["admin", "search-settings"],
    queryFn: () => fetchAdminSearchSettings(client),
  });

  const stackQ = useQuery({
    queryKey: ["admin", "stack-status"],
    queryFn: () => fetchAdminStackStatus(client),
  });

  const stackControlReachable = stackQ.data?.meta.stack_control_reachable ?? false;

  useEffect(() => {
    if (q.data) setRerankerEnabled(q.data.reranker_enabled);
  }, [q.data]);

  const saveMutation = useMutation({
    mutationFn: () => putAdminRerankerEnabled(client, rerankerEnabled),
    onSuccess: (data) => {
      setError(null);
      queryClient.setQueryData(["admin", "search-settings"], data);
      if (!stackControlReachable) {
        setSavedNeedsManualRestart(true);
      }
    },
    onError: (e) => setError(errMsg(e)),
  });

  async function runRestartPoll(): Promise<void> {
    setRestartPhase("polling");
    setRestartStatus("Waiting for stack to come back up…");
    const result = await pollOrchestratorHealth(client);
    if (result.ok) {
      setRestartPhase("done");
      setRestartStatus("Stack restarted successfully.");
      await queryClient.invalidateQueries({ queryKey: ["admin", "search-settings"] });
      return;
    }
    setRestartPhase("timeout");
    setRestartStatus(null);
  }

  const saveAndRestartMutation = useMutation({
    mutationFn: async () => {
      setSavedNeedsManualRestart(false);
      setError(null);
      setRestartPhase("idle");
      setRestartStatus("Saving retrieval settings…");
      const data = await putAdminRerankerEnabled(client, rerankerEnabled);
      queryClient.setQueryData(["admin", "search-settings"], data);
      setRestartStatus("Restarting stack…");
      await restartStack(client);
    },
    onSuccess: () => {
      void runRestartPoll();
    },
    onError: (e) => {
      setRestartPhase("idle");
      setRestartStatus(null);
      setError(errMsg(e));
    },
  });

  if (q.isPending) {
    return (
      <div data-testid="retrieval-settings-page">
        <LoadingPlaceholder label="Loading search settings…" className="max-w-2xl">
          <SkeletonText lines={4} />
        </LoadingPlaceholder>
      </div>
    );
  }

  if (q.isError) {
    return (
      <div data-testid="retrieval-settings-page">
        <ErrorState message={errMsg(q.error)} onRetry={() => void q.refetch()} />
      </div>
    );
  }

  const persisted = q.data;
  const dirty = rerankerEnabled !== persisted.reranker_enabled;
  const liveActive = isRerankerBackendActive(persisted.reranker_backend_live);
  const busy = saveMutation.isPending || saveAndRestartMutation.isPending || restartPhase === "polling";
  const currentMode =
    liveActive && !persisted.reranker_pending_restart
      ? "On"
      : persisted.reranker_enabled
        ? "On after the next restart"
        : "Off";

  return (
    <div data-testid="retrieval-settings-page">
      <PageSection
        title="Better search ranking"
        description="After Lumogis finds possible matches, a second model re-reads them so the best ones come first. Answers get more precise, at the cost of more memory."
      >
        <dl className="m-0">
          <Row label="Better ranking">
            <span className="flex items-center gap-2">
              <Switch
                id="reranker-switch"
                checked={rerankerEnabled}
                disabled={busy}
                onCheckedChange={(on: boolean) => setRerankerEnabled(on)}
                aria-label="Better search ranking"
              />
              <Label htmlFor="reranker-switch" className="font-normal">
                {rerankerEnabled ? "On" : "Off (default, uses less memory)"}
              </Label>
            </span>
          </Row>
          <Row label="Currently">{currentMode}</Row>
        </dl>

        {rerankerEnabled ? (
          <Alert role="note" className="mt-3">
            <AlertDescription>
              Better ranking loads an extra model and adds roughly {BGE_RERANKER_RAM_ESTIMATE} of memory. On a
              memory-constrained host this can degrade or destabilise the stack — turn it off if you hit memory
              pressure. The first time, it downloads about {BGE_RERANKER_DOWNLOAD_ESTIMATE}.
            </AlertDescription>
          </Alert>
        ) : null}

        {persisted.reranker_pending_restart ? (
          <Alert role="status" className="mt-3">
            <AlertDescription>Change pending — restart orchestrator to apply.</AlertDescription>
          </Alert>
        ) : dirty ? (
          <Alert role="status" className="mt-3">
            <AlertDescription>Unsaved change — save (and restart) to apply.</AlertDescription>
          </Alert>
        ) : null}

        {savedNeedsManualRestart && !stackControlReachable ? (
          <Alert role="status" className="mt-3">
            <AlertDescription>
              Settings saved. Restart the Lumogis orchestrator process manually (Docker: recreate the
              orchestrator container; Lumogis Server: restart from the server tray or supervisor).
            </AlertDescription>
          </Alert>
        ) : null}

        {restartPhase === "timeout" ? (
          <Alert role="status" className="mt-3">
            <AlertDescription>
              <p className="m-0">
                The stack may still be starting — this panel only waits <strong>90 seconds</strong> before showing
                this message. If you turned on better ranking, the first model download often takes{" "}
                <strong>several minutes</strong>; that is normal.
              </p>
              <Button type="button" variant="outline" size="sm" className="mt-2" onClick={() => void runRestartPoll()}>
                Check again
              </Button>
            </AlertDescription>
          </Alert>
        ) : restartStatus ? (
          <Alert role="status" className="mt-3">
            <AlertDescription>{restartStatus}</AlertDescription>
          </Alert>
        ) : null}

        {error ? (
          <Alert variant="destructive" className="mt-3">
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        ) : null}

        <div className="mt-4 flex flex-wrap gap-2">
          <Button type="button" disabled={busy || !dirty} onClick={() => saveMutation.mutate()}>
            Save retrieval settings
          </Button>
          {stackControlReachable ? (
            <Button
              type="button"
              variant="outline"
              disabled={busy || !dirty}
              onClick={() => saveAndRestartMutation.mutate()}
            >
              Save &amp; restart stack
            </Button>
          ) : null}
        </div>
      </PageSection>

      <Collapsible className="max-w-2xl">
        <CollapsibleTrigger
          render={<Button type="button" variant="ghost" size="sm" className="-ml-2 group/trigger" />}
        >
          <ChevronDown aria-hidden="true" className="transition-transform group-aria-expanded/trigger:rotate-180" />
          Details
        </CollapsibleTrigger>
        <CollapsibleContent keepMounted className="flex flex-col gap-2 pt-2 text-sm">
          <p className="m-0">
            Model: {BGE_RERANKER_MODEL} (about {BGE_RERANKER_RAM_ESTIMATE} of memory), downloaded from HuggingFace on
            first use. Turning it on or off needs an orchestrator restart.
          </p>
          <p className="m-0 text-muted-foreground">
            To follow the first download, run <code className="font-mono">docker compose logs orchestrator</code>.
          </p>
        </CollapsibleContent>
      </Collapsible>
    </div>
  );
}
