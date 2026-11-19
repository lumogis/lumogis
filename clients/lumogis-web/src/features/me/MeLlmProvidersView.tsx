// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Read-only AI services — GET /api/v1/me/llm-providers. No editing.
//
// Rebuilt on the shared components in LUM-993 plan B Pass 5: one list in plain
// words (name, what it is, connected or not, where its key comes from), with the
// provider id, dates and tier flags under "Details".

import { ChevronDown } from "lucide-react";
import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { ApiError } from "../../api/client";
import { fetchMeLlmProviders, type MeLlmProviderItem } from "../../api/meLlmProviders";
import { useAuth } from "../../auth/AuthProvider";
import { ErrorState } from "../../components/page/ErrorState";
import { LoadingPlaceholder, SkeletonText } from "../../components/page/LoadingState";
import { PageSection } from "../../components/page/PageSection";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "../../components/ui/collapsible";
import { Label } from "../../components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "../../components/ui/select";
import { formatDateTime } from "../../util/formatTimestamp";

const TIER_LABELS: Record<string, string> = {
  user: "Your account",
  household: "Shared by the household",
  system: "Set up by the administrator",
  env: "Server default",
  none: "None",
};

function labelTier(raw: string): string {
  return TIER_LABELS[raw] ?? raw.replace(/_/g, " ");
}

const FILTERS = [
  { value: "all", label: "All" },
  { value: "configured", label: "Connected only" },
  { value: "not_configured", label: "Not connected" },
  { value: "user", label: "Key from your account" },
  { value: "household", label: "Key shared by the household" },
  { value: "system", label: "Key set up by the administrator" },
  { value: "env", label: "Server default key" },
  { value: "none", label: "No key" },
];

const LINK_CLASS = "text-accent-text underline underline-offset-2";

function ProviderRow({ p }: { p: MeLlmProviderItem }): JSX.Element {
  const description =
    p.description && p.description.length > 220 ? `${p.description.slice(0, 217)}…` : p.description;
  return (
    <li data-provider={p.connector} className="grid gap-1 py-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="min-w-0 flex-1 text-sm font-medium">{p.label}</span>
        {p.configured ? (
          <Badge variant="secondary">Connected</Badge>
        ) : (
          <Badge variant="outline">Not connected</Badge>
        )}
      </div>
      {description ? <p className="m-0 text-sm text-muted-foreground">{description}</p> : null}
      {p.configured ? (
        <p className="m-0 text-xs text-muted-foreground">Key from: {labelTier(p.active_tier)}</p>
      ) : null}
      <Collapsible>
        <CollapsibleTrigger
          render={<Button type="button" variant="ghost" size="sm" className="-ml-2 group/trigger" />}
          aria-label={`Details for ${p.label}`}
        >
          <ChevronDown
            aria-hidden="true"
            className="transition-transform group-aria-expanded/trigger:rotate-180"
          />
          Details
        </CollapsibleTrigger>
        <CollapsibleContent className="grid gap-1 pt-1 text-sm">
          <div>
            <span className="text-muted-foreground">Technical name: </span>
            <span className="font-mono break-all">{p.connector}</span>
          </div>
          {p.updated_at ? (
            <div>
              <span className="text-muted-foreground">Last saved: </span>
              {formatDateTime(p.updated_at)}
            </div>
          ) : null}
          {p.key_version != null ? (
            <div>
              <span className="text-muted-foreground">Key version: </span>
              {p.key_version}
            </div>
          ) : null}
          {p.user_credential_present ? <div>You have saved your own key.</div> : null}
          {p.household_credential_available ? <div>The household has a shared key.</div> : null}
          {p.system_credential_available ? <div>The administrator has set up a key.</div> : null}
          {p.env_fallback_available ? <div>A server default key may apply.</div> : null}
          {p.why_not_available ? <div>{p.why_not_available}</div> : null}
        </CollapsibleContent>
      </Collapsible>
    </li>
  );
}

export function MeLlmProvidersView(): JSX.Element {
  const { client } = useAuth();
  const [tierFilter, setTierFilter] = useState<string>("all");

  const q = useQuery({
    queryKey: ["me", "llm-providers"],
    queryFn: () => fetchMeLlmProviders(client),
  });

  const filtered = useMemo(() => {
    const providers = q.data?.providers ?? [];
    if (tierFilter === "all") return providers;
    if (tierFilter === "configured") return providers.filter((p) => p.configured);
    if (tierFilter === "not_configured") return providers.filter((p) => !p.configured);
    return providers.filter((p) => p.active_tier === tierFilter);
  }, [q.data?.providers, tierFilter]);

  if (q.isPending) {
    return (
      <LoadingPlaceholder label="Loading your AI services…" className="max-w-2xl">
        <SkeletonText lines={5} />
      </LoadingPlaceholder>
    );
  }

  if (q.isError) {
    const err = q.error;
    const detail =
      err instanceof ApiError
        ? err.status === 401
          ? "Your session expired or you are not signed in. Refresh the page or sign in again."
          : err.detail
        : "Something went wrong loading your AI services.";
    return <ErrorState message={detail} onRetry={() => void q.refetch()} />;
  }

  const { summary } = q.data;

  return (
    <PageSection
      title="Your AI services"
      description={
        <>
          This list only shows what is connected; it never shows keys. To add or change a key, use{" "}
          <Link to="/settings/connections" className={LINK_CLASS}>
            Connected services
          </Link>
          .
        </>
      }
    >
      <p className="m-0 mb-3 text-sm" data-testid="llm-summary">
        {summary.configured} of {summary.total} connected.
      </p>

      {summary.configured === 0 ? (
        <p className="m-0 mb-3 text-sm text-muted-foreground">
          No AI service has a key yet. Add one under{" "}
          <Link to="/settings/connections" className={LINK_CLASS}>
            Connected services
          </Link>
          .
        </p>
      ) : null}

      <div className="mb-2 flex max-w-xs flex-col gap-1.5">
        <Label htmlFor="llm-filter">Show</Label>
        <Select
          value={tierFilter}
          items={FILTERS}
          onValueChange={(v) => setTierFilter(String(v ?? "all"))}
        >
          <SelectTrigger id="llm-filter" aria-label="Filter AI services" className="w-full">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {FILTERS.map((f) => (
              <SelectItem key={f.value} value={f.value}>
                {f.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      {filtered.length === 0 ? (
        <p className="m-0 py-3 text-sm text-muted-foreground">No AI services match this filter.</p>
      ) : (
        <ul role="list" className="m-0 flex list-none flex-col divide-y divide-border p-0">
          {filtered.map((p) => (
            <ProviderRow key={p.connector} p={p} />
          ))}
        </ul>
      )}

      <Collapsible className="mt-2 border-t pt-2">
        <CollapsibleTrigger
          render={<Button type="button" variant="ghost" size="sm" className="-ml-2 group/trigger" />}
        >
          <ChevronDown
            aria-hidden="true"
            className="transition-transform group-aria-expanded/trigger:rotate-180"
          />
          Details
        </CollapsibleTrigger>
        <CollapsibleContent className="pt-1 text-sm">
          <p className="m-0 mb-1 text-muted-foreground">Where each connected key comes from</p>
          <ul role="list" className="m-0 list-none p-0">
            {Object.entries(summary.by_active_tier)
              .sort(([a], [b]) => a.localeCompare(b))
              .map(([k, n]) => (
                <li key={k}>
                  {labelTier(k)}: {n}
                </li>
              ))}
          </ul>
        </CollapsibleContent>
      </Collapsible>
    </PageSection>
  );
}
