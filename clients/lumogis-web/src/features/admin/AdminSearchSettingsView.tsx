// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Admin Search & retrieval settings (LUM-159). Exposes the BGE reranker
// quality toggle with honest RAM/download copy, pending-vs-live state,
// and restart-and-wait UX on Docker Compose.

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
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
      <section aria-busy="true" data-testid="retrieval-settings-page">
        <h1>Search &amp; retrieval</h1>
        <p>Loading…</p>
      </section>
    );
  }

  if (q.isError) {
    return (
      <section data-testid="retrieval-settings-page">
        <h1>Search &amp; retrieval</h1>
        <p role="alert">{errMsg(q.error)}</p>
      </section>
    );
  }

  const persisted = q.data;
  const dirty = rerankerEnabled !== persisted.reranker_enabled;
  const liveActive = isRerankerBackendActive(persisted.reranker_backend_live);
  const busy = saveMutation.isPending || saveAndRestartMutation.isPending || restartPhase === "polling";

  return (
    <section data-testid="retrieval-settings-page">
      <h1>Search &amp; retrieval</h1>
      <p style={{ maxWidth: "42rem", opacity: 0.9 }}>
        Two-stage retrieval: after vector search finds candidates, a cross-encoder re-scores them
        for higher precision. Downloads {BGE_RERANKER_DOWNLOAD_ESTIMATE} from HuggingFace on
        first enable. Requires restart.
      </p>

      <fieldset style={{ border: "none", padding: 0, marginTop: "1rem" }}>
        <legend className="sr-only">Reranking mode</legend>
        <label style={{ display: "block", marginBottom: "0.5rem" }}>
          <input
            type="radio"
            name="reranking"
            value="off"
            checked={!rerankerEnabled}
            disabled={busy}
            onChange={() => setRerankerEnabled(false)}
          />{" "}
          Off — default, lower RAM
        </label>
        <label style={{ display: "block", marginBottom: "0.75rem" }}>
          <input
            type="radio"
            name="reranking"
            value="bge"
            checked={rerankerEnabled}
            disabled={busy}
            onChange={() => setRerankerEnabled(true)}
          />{" "}
          BGE reranker ({BGE_RERANKER_MODEL}, {BGE_RERANKER_RAM_ESTIMATE})
        </label>
      </fieldset>

      {rerankerEnabled ? (
        <p role="note" style={{ maxWidth: "42rem", fontSize: "0.9rem", color: "#ed6c02" }}>
          The BGE reranker loads {BGE_RERANKER_MODEL} and adds roughly {BGE_RERANKER_RAM_ESTIMATE}{" "}
          of resident memory. On a memory-constrained host this can degrade or destabilise the
          stack — turn it off if you hit memory pressure.
        </p>
      ) : null}

      <p style={{ fontSize: "0.85rem", opacity: 0.85 }}>
        Current mode:{" "}
        <strong>{liveActive && !persisted.reranker_pending_restart ? "BGE reranker (live)" : persisted.reranker_enabled ? "BGE reranker (pending restart)" : "Off"}</strong>
      </p>

      {persisted.reranker_pending_restart ? (
        <p role="status" style={{ fontSize: "0.85rem", color: "#ed6c02" }}>
          Change pending — restart orchestrator to apply.
        </p>
      ) : dirty ? (
        <p role="status" style={{ fontSize: "0.85rem", color: "#ed6c02" }}>
          Unsaved change — save (and restart) to apply.
        </p>
      ) : null}

      {savedNeedsManualRestart && !stackControlReachable ? (
        <p role="status" style={{ maxWidth: "42rem", fontSize: "0.85rem", color: "#ed6c02" }}>
          Settings saved. Restart the Lumogis orchestrator process manually (Docker: recreate the
          orchestrator container; Lumogis Server: restart from the server tray or supervisor).
        </p>
      ) : null}

      {restartPhase === "timeout" ? (
        <div role="status" style={{ maxWidth: "42rem", fontSize: "0.85rem", marginTop: "0.5rem" }}>
          <p>
            The stack may still be starting — this panel only waits <strong>90 seconds</strong>{" "}
            before showing this message. If you enabled the <strong>BGE reranker</strong>, the first
            model download often takes <strong>several minutes</strong>; that is normal. Check{" "}
            <code>docker compose logs orchestrator</code> for progress.
          </p>
          <button type="button" onClick={() => void runRestartPoll()}>
            Check again
          </button>
        </div>
      ) : restartStatus ? (
        <p role="status" style={{ fontSize: "0.85rem", marginTop: "0.5rem" }}>
          {restartStatus}
        </p>
      ) : null}

      {error ? (
        <p role="alert" style={{ color: "#c62828" }}>
          {error}
        </p>
      ) : null}

      <div style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap", marginTop: "0.75rem" }}>
        <button
          type="button"
          disabled={busy || !dirty}
          onClick={() => saveMutation.mutate()}
        >
          Save retrieval settings
        </button>
        {stackControlReachable ? (
          <button
            type="button"
            disabled={busy || !dirty}
            onClick={() => saveAndRestartMutation.mutate()}
          >
            Save &amp; restart stack
          </button>
        ) : null}
      </div>
    </section>
  );
}
