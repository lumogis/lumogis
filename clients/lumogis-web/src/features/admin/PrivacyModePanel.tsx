// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import {
  type AdminSettingsPrivacy,
  type InstancePrivacyMode,
  putAdminPrivacySettings,
} from "../../api/privacyMode";
import { ApiError } from "../../api/client";
import { useAuth } from "../../auth/AuthProvider";

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
    onSuccess: () => {
      setError(null);
      void queryClient.invalidateQueries({ queryKey: ["admin", "settings"] });
    },
    onError: (e) => setError(errMsg(e)),
  });

  return (
    <div>
      <h2>Privacy mode</h2>
      <p style={{ maxWidth: "42rem", opacity: 0.9 }}>
        Control whether cloud LLM providers may be used. This is routing policy at the
        orchestrator — it does not guarantee network isolation. Local-only mode blocks
        remote models from being listed or invoked; chat requests fall back to your local
        model with an explicit warning.
      </p>
      <fieldset style={{ border: "none", padding: 0, marginTop: "1rem" }}>
        <legend className="sr-only">Household privacy mode</legend>
        <label style={{ display: "block", marginBottom: "0.5rem" }}>
          <input
            type="radio"
            name="privacy_mode"
            value="local_only"
            checked={mode === "local_only"}
            onChange={() => setMode("local_only")}
          />{" "}
          Local only — no cloud LLM routing
        </label>
        <label style={{ display: "block", marginBottom: "0.75rem" }}>
          <input
            type="radio"
            name="privacy_mode"
            value="allow_cloud"
            checked={mode === "allow_cloud"}
            disabled={initial.privacy_mode_locked && initial.privacy_mode === "local_only"}
            onChange={() => setMode("allow_cloud")}
          />{" "}
          Allow cloud — users may use configured cloud models
        </label>
        <label style={{ display: "block", marginBottom: "1rem" }}>
          <input
            type="checkbox"
            checked={locked}
            disabled={mode === "allow_cloud"}
            onChange={(e) => setLocked(e.target.checked)}
          />{" "}
          Lock local-only (prevents household from enabling cloud until unlocked)
        </label>
      </fieldset>
      <p style={{ fontSize: "0.85rem", opacity: 0.85 }}>
        Effective household policy: <strong>{initial.privacy_effective.replace("_", " ")}</strong>
      </p>
      {error ? (
        <p role="alert" style={{ color: "#c62828" }}>
          {error}
        </p>
      ) : null}
      <button type="button" disabled={mutation.isPending} onClick={() => mutation.mutate()}>
        Save privacy settings
      </button>
    </div>
  );
}
