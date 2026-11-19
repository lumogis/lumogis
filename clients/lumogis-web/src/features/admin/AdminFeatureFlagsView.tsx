// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Admin feature-flags: read-only visibility onto the env-var experimental
// gates registry (LUM-126). Surfacing for LUM-573 — operators can see which
// disabled-by-default flags exist and which are active without grepping env
// files. Env-only in v1: this panel does not mutate flags.
//
// Rebuilt on the shared components in LUM-993 plan B Pass 6: one row per flag with a
// state as a badge (flags are set in the server's environment; there is no
// toggle here, so no control that looks usable), its description and env var name.

import { useQuery } from "@tanstack/react-query";

import { fetchFeatureFlags, type FeatureFlagState } from "../../api/featureFlags";
import { ApiError } from "../../api/client";
import { useAuth } from "../../auth/AuthProvider";
import { ErrorState } from "../../components/page/ErrorState";
import { LoadingPlaceholder, SkeletonText } from "../../components/page/LoadingState";
import { PageSection } from "../../components/page/PageSection";
import { Alert, AlertDescription } from "../../components/ui/alert";
import { Badge } from "../../components/ui/badge";

// Per-flag honesty copy shown under the description. Keyed by registry key so
// the note travels with the flag regardless of table order.
const FLAG_NOTES: Record<string, string> = {
  EGRESS_GUARD:
    "Bypassable defence-in-depth — routing policy at the orchestrator remains the primary guarantee. Enabling this does not by itself isolate the network.",
};

export function AdminFeatureFlagsView(): JSX.Element {
  const { client } = useAuth();
  const q = useQuery({
    queryKey: ["admin", "feature-flags"],
    queryFn: () => fetchFeatureFlags(client),
  });

  if (q.isPending) {
    return (
      <LoadingPlaceholder label="Loading feature flags…" className="max-w-2xl">
        <SkeletonText lines={5} />
      </LoadingPlaceholder>
    );
  }

  if (q.isError) {
    const err = q.error;
    const detail =
      err instanceof ApiError
        ? err.status === 401
          ? "Not signed in or session expired."
          : err.status === 403
            ? "Admin role required for feature flags."
            : err.detail
        : "Feature flags unavailable.";
    return <ErrorState message={detail} onRetry={() => void q.refetch()} />;
  }

  const data = q.data;

  return (
    <PageSection
      title="Experimental features"
      description={
        <>
          Experimental subsystems ship disabled by default and are gated by environment variables. This is a
          read-only window onto the registry — it shows which flags exist and which are active in this process. Flags
          are <strong>set in the host environment</strong> (env file or Compose) and take effect on the next
          orchestrator restart; there is no in-product toggle in this release.
        </>
      }
    >
      <p className="m-0 mb-2 text-sm text-muted-foreground">
        {data.enabled} of {data.total} flag{data.total === 1 ? "" : "s"} currently enabled.
      </p>
      <ul className="m-0 list-none p-0">
        {data.flags.map((flag: FeatureFlagState) => {
          const note = FLAG_NOTES[flag.key];
          const labelId = `flag-${flag.key}`;
          return (
            <li key={flag.key} className="flex items-start gap-3 border-b py-3 last:border-b-0">
              <div className="flex min-w-0 flex-col gap-1 text-sm">
                <span className="flex flex-wrap items-baseline gap-x-2">
                  <span id={labelId} className="font-mono font-medium break-all">
                    {flag.key}
                  </span>
                  <Badge variant={flag.enabled ? "secondary" : "outline"} data-testid={`flag-state-${flag.key}`}>
                    {flag.enabled ? "enabled" : "disabled"}
                  </Badge>
                </span>
                <span className="text-xs text-muted-foreground">{flag.description}</span>
                {note ? (
                  <Alert role="note" className="mt-1">
                    <AlertDescription>{note}</AlertDescription>
                  </Alert>
                ) : null}
                <span className="font-mono text-xs break-all text-muted-foreground">{flag.env_var}</span>
              </div>
            </li>
          );
        })}
      </ul>
    </PageSection>
  );
}
