// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { fetchMePrivacyMode, patchMePrivacyMode } from "../../api/privacyMode";
import { ApiError } from "../../api/client";
import { useAuth } from "../../auth/AuthProvider";

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

  if (q.isLoading) return <p>Loading…</p>;
  if (q.isError) return <p role="alert">{errMsg(q.error)}</p>;

  const data = q.data!;
  const furtherRestrict = data.user_restriction === "local_only";
  const canToggle =
    data.instance.privacy_effective === "allow_cloud" && !data.instance.privacy_mode_locked;

  return (
    <section>
      <h1>Privacy mode</h1>
      <p style={{ maxWidth: "42rem", opacity: 0.9 }}>
        Household policy: <strong>{data.instance.privacy_effective.replace("_", " ")}</strong>.
        Your effective policy: <strong>{data.privacy_effective.replace("_", " ")}</strong>.
      </p>
      {canToggle ? (
        <label style={{ display: "block", marginTop: "1rem" }}>
          <input
            type="checkbox"
            checked={furtherRestrict}
            disabled={mutation.isPending}
            onChange={(e) => mutation.mutate(e.target.checked)}
          />{" "}
          Further restrict to local-only (you cannot enable cloud beyond household policy)
        </label>
      ) : (
        <p style={{ marginTop: "1rem", opacity: 0.85 }}>
          {data.instance.privacy_mode_locked
            ? "Household privacy mode is locked to local-only."
            : "Household policy is already local-only; per-user cloud access is not available."}
        </p>
      )}
      {mutation.isError ? (
        <p role="alert" style={{ color: "#c62828", marginTop: "0.75rem" }}>
          {errMsg(mutation.error)}
        </p>
      ) : null}
    </section>
  );
}
