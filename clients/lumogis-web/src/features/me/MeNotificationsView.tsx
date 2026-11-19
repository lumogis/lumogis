// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Notification settings — GET /api/v1/me/notifications (channel status, read-only).
// Phase 4C: enrol + manage browser Web Push in PushOptIn.
// Edit ntfy credentials under Settings → Connectors.
//
// Rebuilt on the shared components in LUM-993 plan B Pass 5: plain choices first
// (this device, ntfy, what notifies you, quiet hours); tiers, ids, VAPID and
// credential flags live in a collapsed "Details" for whoever runs the server.

import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ChevronDown } from "lucide-react";

import { ApiError } from "../../api/client";
import { fetchMeNotifications } from "../../api/meNotifications";
import { fetchNotificationPreferences } from "../../api/notificationPreferences";
import { useAuth } from "../../auth/AuthProvider";
import { ErrorState } from "../../components/page/ErrorState";
import { LoadingPlaceholder, SkeletonText } from "../../components/page/LoadingState";
import { PageSection } from "../../components/page/PageSection";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "../../components/ui/collapsible";
import { Label } from "../../components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "../../components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "../../components/ui/table";
import { formatDateTime } from "../../util/formatTimestamp";
import { NotificationPrefsEditor, NotificationTierDetails } from "./NotificationPrefsEditor";
import { PushOptIn } from "./PushOptIn";

const TIER_LABELS: Record<string, string> = {
  user: "Your account",
  household: "Household shared",
  system: "Instance / system",
  env: "Environment fallback",
  none: "None",
};

function labelTier(raw: string): string {
  return TIER_LABELS[raw] ?? raw.replace(/_/g, " ");
}

function triState(v: boolean | null | undefined): string {
  if (v === true) return "Yes";
  if (v === false) return "No";
  return "Unknown (encrypted)";
}

const FILTER_LABELS: Record<string, string> = {
  all: "All",
  configured: "Configured only",
  not_configured: "Not configured",
  user: "Active tier: your account",
  household: "Active tier: household",
  system: "Active tier: system",
  env: "Active tier: env fallback",
  none: "Active tier: none",
};

function StatusBadge({ status }: { status: string }): JSX.Element {
  if (status === "paused") return <Badge variant="outline">Paused</Badge>;
  if (status === "configured") return <Badge variant="secondary">Set up</Badge>;
  return <Badge variant="outline">Not set up</Badge>;
}

export function MeNotificationsView(): JSX.Element {
  const { client } = useAuth();
  const [filter, setFilter] = useState<string>("all");

  const q = useQuery({
    queryKey: ["me", "notifications"],
    queryFn: () => fetchMeNotifications(client),
  });

  const prefsQ = useQuery({
    queryKey: ["me", "notification-preferences"],
    queryFn: () => fetchNotificationPreferences(client),
  });

  const filtered = useMemo(() => {
    const channels = q.data?.channels ?? [];
    if (filter === "all") return channels;
    if (filter === "configured") return channels.filter((c) => c.status === "configured");
    if (filter === "not_configured") return channels.filter((c) => c.status === "not_configured");
    return channels.filter((c) => c.active_tier === filter);
  }, [q.data?.channels, filter]);

  if (q.isPending) {
    return (
      <LoadingPlaceholder label="Loading your notification settings…" className="max-w-2xl">
        <SkeletonText lines={6} />
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
        : "Something went wrong loading notification status.";
    return (
      <ErrorState
        message={typeof detail === "string" ? detail : "Lumogis couldn't load your notification settings."}
        onRetry={() => void q.refetch()}
      />
    );
  }

  const { summary, channels } = q.data!;
  const ntfy = channels.find((c) => c.connector === "ntfy");

  return (
    <div className="flex flex-col gap-2">
      <PushOptIn />

      <PageSection
        title="Notifications on your phone"
        description="ntfy is a free app that shows Lumogis notifications on your phone, even when no browser is open."
      >
        {ntfy ? (
          <p className="mt-0 mb-3 flex items-center gap-2 text-sm">
            Status: <StatusBadge status={ntfy.status} />
          </p>
        ) : null}
        <ol className="m-0 flex list-decimal flex-col gap-1.5 pl-5 text-sm">
          <li>Install the ntfy app on your phone.</li>
          <li>
            Add your ntfy server address and topic under{" "}
            <a href="/settings/connections" className="text-accent-text underline underline-offset-2">
              Connected services
            </a>
            .
          </li>
          <li>Come back here and tick ntfy for the news you want on your phone.</li>
        </ol>
      </PageSection>

      {prefsQ.isPending ? (
        <LoadingPlaceholder label="Loading your choices…" className="max-w-2xl">
          <SkeletonText lines={4} />
        </LoadingPlaceholder>
      ) : prefsQ.isError ? (
        <ErrorState message="Lumogis couldn't load your notification choices." onRetry={() => void prefsQ.refetch()} />
      ) : prefsQ.data ? (
        <NotificationPrefsEditor client={client} prefs={prefsQ.data} />
      ) : null}

      <Collapsible className="max-w-2xl">
        <CollapsibleTrigger
          render={<Button type="button" variant="ghost" size="sm" className="-ml-2 group/trigger" />}
        >
          <ChevronDown
            aria-hidden="true"
            className="transition-transform group-aria-expanded/trigger:rotate-180"
          />
          Details
        </CollapsibleTrigger>
        {/* keepMounted: the channel table stays in the page (as it was) while closed. */}
        <CollapsibleContent keepMounted className="flex flex-col gap-4 pt-2 text-sm">
          <p className="m-0 text-muted-foreground">
            For whoever runs Lumogis. This page does not send test notifications or reveal tokens.
          </p>

          <p className="m-0">
            {summary.total} channels, {summary.configured} set up, {summary.not_configured} not set up,{" "}
            {summary.paused} paused.
          </p>

          <div>
            <h3 className="m-0 mb-1 text-sm font-semibold">By active tier</h3>
            <ul role="list" className="m-0 list-none p-0">
              {Object.entries(summary.by_active_tier)
                .sort(([a], [b]) => a.localeCompare(b))
                .map(([k, n]) => (
                  <li key={k}>
                    {labelTier(k)}: {n}
                  </li>
                ))}
            </ul>
          </div>

          {prefsQ.data ? <NotificationTierDetails prefs={prefsQ.data} /> : null}

          <div className="flex flex-col gap-1.5">
            <Label htmlFor="channel-filter">Filter</Label>
            <Select value={filter} onValueChange={(v) => setFilter(String(v ?? "all"))}>
              <SelectTrigger id="channel-filter" aria-label="Filter channels" className="w-64">
                <SelectValue>{(v: string | null) => FILTER_LABELS[v ?? "all"] ?? "All"}</SelectValue>
              </SelectTrigger>
              <SelectContent>
                {Object.entries(FILTER_LABELS).map(([value, label]) => (
                  <SelectItem key={value} value={value}>
                    {label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Channel</TableHead>
                <TableHead>Id</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Tier</TableHead>
                <TableHead>Details</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {filtered.map((c) => (
                <TableRow key={c.connector} className="align-top">
                  <TableCell className="whitespace-normal">
                    <div className="font-medium">{c.label}</div>
                    {c.description ? (
                      <div className="mt-1 text-xs text-muted-foreground">
                        {c.description.length > 200 ? `${c.description.slice(0, 197)}…` : c.description}
                      </div>
                    ) : null}
                  </TableCell>
                  <TableCell className="font-mono text-xs">{c.connector}</TableCell>
                  <TableCell>
                    {c.status === "paused" ? "Paused" : c.status === "configured" ? "Configured" : "Not configured"}
                  </TableCell>
                  <TableCell>{labelTier(c.active_tier)}</TableCell>
                  <TableCell className="text-xs whitespace-normal">
                    {c.url ? <div>Server URL: {c.url}</div> : null}
                    <div>URL set: {triState(c.url_configured)}</div>
                    <div>Topic set: {triState(c.topic_configured)}</div>
                    <div>Token set: {triState(c.token_configured)}</div>
                    {c.subscription_count != null ? <div>Browser subscriptions: {c.subscription_count}</div> : null}
                    {c.push_service_configured != null ? (
                      <div>VAPID / push service ready: {c.push_service_configured ? "Yes" : "No"}</div>
                    ) : null}
                    {c.updated_at ? <div>Updated: {formatDateTime(c.updated_at)}</div> : null}
                    {c.key_version != null ? <div>Key version: {c.key_version}</div> : null}
                    {c.why_not_available ? <div>{c.why_not_available}</div> : null}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CollapsibleContent>
      </Collapsible>
    </div>
  );
}
