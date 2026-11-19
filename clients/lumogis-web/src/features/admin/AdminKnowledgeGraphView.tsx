// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Admin → Knowledge graph (LUM-937, LUM-989): explains the optional knowledge
// graph, says whether it is on and whether this machine has enough memory, and
// on a Release install turns it on and off with one click. The server computes
// the state and which buttons exist (`turn_on`, `turn_off`); the words live in
// knowledgeGraphCopy.ts.
//
// Rebuilt on the shared components in LUM-993 plan B Pass 6 (the words are unchanged):
// status and buttons in one section, the explainer sections after it, turning off
// confirmed in an AlertDialog. The written steps for a source checkout stay visible
// because they are the page's only way to turn it on there.

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { cn } from "@/lib/utils";

import { ApiError } from "../../api/client";
import {
  disableKnowledgeGraph,
  enableKnowledgeGraph,
  fetchKnowledgeGraphStatus,
  isTransientState,
  KNOWLEDGE_GRAPH_POLL_MS,
  KNOWLEDGE_GRAPH_QUERY_KEY,
} from "../../api/knowledgeGraph";
import { useAuth } from "../../auth/AuthProvider";
import { ErrorState } from "../../components/page/ErrorState";
import { LoadingPlaceholder, SkeletonText } from "../../components/page/LoadingState";
import { PageSection } from "../../components/page/PageSection";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "../../components/ui/alert-dialog";
import { Alert, AlertDescription } from "../../components/ui/alert";
import { Button } from "../../components/ui/button";
import { Checkbox } from "../../components/ui/checkbox";
import { Label } from "../../components/ui/label";
import {
  ADMIN_DIAGNOSTICS_PATH,
  ENABLE_ENV_LINES,
  KG_DOCS_URL,
  TURN_OFF_CONFIRMATION,
  knowledgeGraphCopy,
  lowMemoryConfirmation,
  turnOffLabel,
  turnOnLabel,
} from "./knowledgeGraphCopy";

function actionError(e: unknown): string {
  if (e instanceof ApiError && e.status === 409) {
    if (e.detail === "busy") return "The graph is already starting or stopping. Wait for it to finish.";
    if (e.detail === "blocked") return "The graph cannot be changed from here right now. See the note on this page.";
    if (e.detail === "needs_confirmation") return "Confirm first that you want to turn it on on this machine.";
  }
  if (e instanceof ApiError && e.status === 403) return "Admin role required.";
  return "That did not work. Try again.";
}

export function AdminKnowledgeGraphView(): JSX.Element {
  const { client } = useAuth();
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: KNOWLEDGE_GRAPH_QUERY_KEY,
    queryFn: () => fetchKnowledgeGraphStatus(client),
    // Keep polling through request errors: Core is recreated at the end of the
    // sequence, so a few requests fail before the new Core answers. `data` still
    // holds the last answer, so the state stays transient and the poll goes on.
    refetchInterval: (query) => {
      const data = query.state.data;
      return data && isTransientState(data.state) ? KNOWLEDGE_GRAPH_POLL_MS : false;
    },
  });
  const [confirmingOn, setConfirmingOn] = useState(false);
  const [understood, setUnderstood] = useState(false);
  const [confirmingOff, setConfirmingOff] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  const refresh = () => qc.invalidateQueries({ queryKey: KNOWLEDGE_GRAPH_QUERY_KEY });
  const enable = useMutation({
    mutationFn: (confirm: boolean) => enableKnowledgeGraph(client, confirm),
    onSuccess: () => {
      setMessage(null);
      setConfirmingOn(false);
      setUnderstood(false);
      void refresh();
    },
    onError: (e) => {
      if (e instanceof ApiError && e.status === 409 && e.detail === "needs_confirmation") {
        setConfirmingOn(true);
      }
      setMessage(actionError(e));
      void refresh();
    },
  });
  const disable = useMutation({
    mutationFn: () => disableKnowledgeGraph(client),
    onSuccess: () => {
      setMessage(null);
      setConfirmingOff(false);
      void refresh();
    },
    onError: (e) => {
      setConfirmingOff(false);
      setMessage(actionError(e));
      void refresh();
    },
  });

  if (q.isPending) {
    return (
      <LoadingPlaceholder label="Loading the knowledge graph…" className="max-w-2xl">
        <SkeletonText lines={5} />
      </LoadingPlaceholder>
    );
  }

  // While the graph changes Core restarts and requests fail for a moment: keep the
  // last answer on screen rather than flashing an error.
  if (q.isError && !q.data) {
    const err = q.error;
    const detail =
      err instanceof ApiError
        ? err.status === 401
          ? "Not signed in or session expired."
          : err.status === 403
            ? "Admin role required for the knowledge graph page."
            : err.detail
        : "Knowledge graph status unavailable.";
    return <ErrorState message={detail} onRetry={() => void q.refetch()} />;
  }

  const status = q.data!;
  const copy = knowledgeGraphCopy(status);
  const busy = enable.isPending || disable.isPending;
  const restarting = q.isError && isTransientState(status.state);
  const confirmation = lowMemoryConfirmation(status);

  const onTurnOn = () => {
    setMessage(null);
    if (status.turn_on.needs_confirmation) {
      setConfirmingOn(true);
      return;
    }
    enable.mutate(false);
  };

  return (
    <div data-testid="knowledge-graph-page" className="min-w-0 max-w-full">
      <PageSection title="Status">
        <p
          role="status"
          data-tone={copy.tone}
          className={cn(
            "m-0 border-l-[3px] border-current pl-2 text-sm font-semibold",
            copy.tone === "ok" && "text-success",
            copy.tone === "info" && "text-accent-text",
            copy.tone === "warn" && "text-warning",
          )}
        >
          {copy.headline}
        </p>
        <div className="mt-3 flex flex-col gap-2 text-sm">
          {restarting ? <p className="m-0">Lumogis is restarting. This page keeps checking.</p> : null}
          {copy.details.map((line) => (
            <p key={line} className="m-0">
              {line}
            </p>
          ))}
          {copy.showDiagnosticsLink ? (
            <p className="m-0">
              <Link to={ADMIN_DIAGNOSTICS_PATH} className="text-accent-text underline underline-offset-2">
                Open Diagnostics
              </Link>
            </p>
          ) : null}
          {copy.blockText ? (
            <Alert role="note">
              <AlertDescription>{copy.blockText}</AlertDescription>
            </Alert>
          ) : null}
          {message ? (
            <Alert variant="destructive">
              <AlertDescription>{message}</AlertDescription>
            </Alert>
          ) : null}
        </div>

        {status.turn_on.available && !confirmingOn ? (
          <div className="mt-3">
            <Button type="button" onClick={onTurnOn} disabled={busy}>
              {turnOnLabel(status)}
            </Button>
          </div>
        ) : null}

        {confirmingOn ? (
          <div
            role="group"
            aria-label="Confirm turning on"
            data-testid="kg-low-memory"
            className="mt-3 flex flex-col gap-3 rounded-lg border p-3 text-sm"
          >
            {confirmation.lines.map((line) => (
              <p key={line} className="m-0">
                {line}
              </p>
            ))}
            <div className="flex items-start gap-2">
              <Checkbox
                id="kg-understood"
                checked={understood}
                onCheckedChange={(checked) => setUnderstood(checked === true)}
              />
              <Label htmlFor="kg-understood" className="font-normal">
                {confirmation.checkbox}
              </Label>
            </div>
            <div className="flex flex-wrap gap-2">
              <Button type="button" disabled={!understood || busy} onClick={() => enable.mutate(true)}>
                Turn on anyway
              </Button>
              <Button
                type="button"
                variant="outline"
                disabled={busy}
                onClick={() => {
                  setConfirmingOn(false);
                  setUnderstood(false);
                }}
              >
                Cancel
              </Button>
            </div>
          </div>
        ) : null}

        {status.turn_off.available ? (
          <div className="mt-3">
            <Button type="button" variant="outline" onClick={() => setConfirmingOff(true)} disabled={busy}>
              {turnOffLabel(status)}
            </Button>
          </div>
        ) : null}
      </PageSection>

      {copy.offer ? (
        <div data-testid="kg-offer">
          <PageSection title={copy.offer.heading}>
            <p className="m-0 text-sm">{copy.offer.text}</p>
          </PageSection>
        </div>
      ) : null}

      <PageSection title="What the graph adds">
        <p className="m-0 text-sm">{copy.adds}</p>
      </PageSection>

      <PageSection title="What it needs">
        <div className="flex flex-col gap-2 text-sm">
          <p className="m-0">{copy.needs}</p>
          {copy.memoryLine !== null ? <p className="m-0">{copy.memoryLine}</p> : null}
        </div>
      </PageSection>

      {copy.showSteps ? (
        <PageSection title="How to turn it on (source checkout)">
          <ol className="m-0 flex list-decimal flex-col gap-2 pl-5 text-sm">
            <li>
              In the .env file in your Lumogis folder, set these lines. If COMPOSE_FILE already
              lists other files (for example docker-compose.gpu.yml), keep them and add the two
              graph files to the end. If CAPABILITY_SERVICE_URLS already has a value, add the URL
              after a comma:
              <pre className="mt-2 overflow-x-auto rounded-md bg-muted p-2 font-mono text-xs">
                {ENABLE_ENV_LINES.join("\n")}
              </pre>
            </li>
            <li>Make the secret with: openssl rand -hex 32</li>
            <li>
              Run docker compose up -d in the Lumogis folder. The first start builds the graph
              service and takes a few minutes.
            </li>
            <li>
              Reload this page. It says the graph is on once Lumogis can reach the graph service
              (allow up to five minutes).
            </li>
          </ol>
        </PageSection>
      ) : null}

      <div className="flex max-w-2xl flex-col gap-2 text-sm">
        {copy.checkoutNote ? <p className="m-0 text-muted-foreground">{copy.checkoutNote}</p> : null}
        <p className="m-0">
          <a
            href={KG_DOCS_URL}
            target="_blank"
            rel="noreferrer"
            className="text-accent-text underline underline-offset-2"
          >
            Turn on the knowledge graph (full steps)
          </a>
        </p>
      </div>

      <AlertDialog open={confirmingOff} onOpenChange={setConfirmingOff}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Turn off the knowledge graph?</AlertDialogTitle>
            <AlertDialogDescription>{TURN_OFF_CONFIRMATION}</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={busy}>Cancel</AlertDialogCancel>
            <AlertDialogAction variant="destructiveSolid" disabled={busy} onClick={() => disable.mutate()}>
              Yes, turn off
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
