// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Read-only “Tools & capabilities” — GET /api/v1/me/tools. No execution.
//
// Rebuilt on the shared components in LUM-993 plan B Pass 6. A developer page: every
// column stays visible, now in a Table with Badges and Select filters.

import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { ApiError } from "../../api/client";
import { fetchMeTools } from "../../api/meTools";
import { useAuth } from "../../auth/AuthProvider";
import { EmptyState } from "../../components/page/EmptyState";
import { ErrorState } from "../../components/page/ErrorState";
import { LoadingPlaceholder, SkeletonText } from "../../components/page/LoadingState";
import { PageSection } from "../../components/page/PageSection";
import { Badge } from "../../components/ui/badge";
import { Label } from "../../components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "../../components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../../components/ui/table";

const SOURCE_LABELS: Record<string, string> = {
  core: "Core",
  plugin: "Plugin",
  mcp: "MCP",
  proxy: "Proxy",
  capability: "Capability",
  action: "Action",
};

const TRANSPORT_LABELS: Record<string, string> = {
  llm_loop: "Chat / assistant",
  mcp_surface: "MCP clients",
  catalog_only: "Listed only",
  both: "Multiple surfaces",
};

const SOURCE_FILTERS: Record<string, string> = {
  all: "All",
  core: "Core",
  plugin: "Plugin",
  mcp: "MCP",
  proxy: "Proxy",
  capability: "Capability",
  action: "Action",
};

const AVAIL_FILTERS: Record<string, string> = {
  all: "All",
  available: "Available",
  unavailable: "Unavailable",
};

function labelSource(raw: string): string {
  return SOURCE_LABELS[raw] ?? raw;
}

function labelTransport(raw: string): string {
  return TRANSPORT_LABELS[raw] ?? raw.replace(/_/g, " ");
}

function labelOriginTier(raw: string): string {
  return raw.replace(/_/g, " ");
}

export function MeToolsCapabilitiesView(): JSX.Element {
  const { client } = useAuth();
  const [sourceFilter, setSourceFilter] = useState<string>("all");
  const [availFilter, setAvailFilter] = useState<string>("all");

  const q = useQuery({
    queryKey: ["me", "tools"],
    queryFn: () => fetchMeTools(client),
  });

  const filtered = useMemo(() => {
    const tools = q.data?.tools ?? [];
    return tools.filter((t) => {
      if (sourceFilter !== "all" && t.source !== sourceFilter) return false;
      if (availFilter === "available" && !t.available) return false;
      if (availFilter === "unavailable" && t.available) return false;
      return true;
    });
  }, [q.data?.tools, sourceFilter, availFilter]);

  if (q.isPending) {
    return (
      <LoadingPlaceholder label="Loading tools…" className="max-w-2xl">
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
        : "Something went wrong loading the catalog.";
    return <ErrorState message={detail} onRetry={() => void q.refetch()} />;
  }

  const { summary } = q.data!;
  const sourceKeys = Object.keys(summary.by_source).sort();

  return (
    <div>
      <PageSection
        title="What your assistant can use"
        description="What your assistant can use right now — read-only overview. This does not run tools or change permissions."
      >
        <div className="grid grid-cols-[repeat(auto-fill,minmax(9rem,1fr))] gap-3">
          {(
            [
              ["Total", "Total tools", summary.total],
              ["Available", "Available tools", summary.available],
              ["Unavailable", "Unavailable tools", summary.unavailable],
            ] as const
          ).map(([label, aria, n]) => (
            <div key={label} className="rounded-lg border p-3">
              <div className="text-xs text-muted-foreground">{label}</div>
              <div className="text-xl font-bold" aria-label={`${aria}: ${n}`}>
                {n}
              </div>
            </div>
          ))}
        </div>

        <h3 className="mt-4 mb-1 text-sm font-semibold">By source</h3>
        <ul className="m-0 list-disc pl-5 text-sm">
          {sourceKeys.map((k) => (
            <li key={k}>
              {labelSource(k)}: {summary.by_source[k]}
            </li>
          ))}
        </ul>
      </PageSection>

      <div className="mb-4 flex flex-wrap gap-4">
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="tools-source-filter">Source</Label>
          <Select value={sourceFilter} onValueChange={(v) => setSourceFilter(String(v ?? "all"))}>
            <SelectTrigger id="tools-source-filter" aria-label="Filter by source" className="w-44">
              <SelectValue>{(v: string | null) => SOURCE_FILTERS[v ?? "all"] ?? "All"}</SelectValue>
            </SelectTrigger>
            <SelectContent>
              {Object.entries(SOURCE_FILTERS).map(([value, label]) => (
                <SelectItem key={value} value={value}>
                  {label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="tools-avail-filter">Availability</Label>
          <Select value={availFilter} onValueChange={(v) => setAvailFilter(String(v ?? "all"))}>
            <SelectTrigger id="tools-avail-filter" aria-label="Filter by availability" className="w-44">
              <SelectValue>{(v: string | null) => AVAIL_FILTERS[v ?? "all"] ?? "All"}</SelectValue>
            </SelectTrigger>
            <SelectContent>
              {Object.entries(AVAIL_FILTERS).map(([value, label]) => (
                <SelectItem key={value} value={value}>
                  {label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </div>

      {summary.total === 0 ? (
        <EmptyState title="No tools are listed in the catalog yet." />
      ) : filtered.length === 0 ? (
        <EmptyState title="No tools match the current filters." />
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Tool</TableHead>
              <TableHead>Source</TableHead>
              <TableHead>Surface</TableHead>
              <TableHead>Tier</TableHead>
              <TableHead>Status</TableHead>
              <TableHead>Details</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {filtered.map((t) => (
              <TableRow key={`${t.source}-${t.name}-${t.capability_id ?? ""}`} className="align-top">
                <TableCell className="whitespace-normal">
                  <div className="font-semibold">{t.label}</div>
                  <div className="text-xs text-muted-foreground">{t.name}</div>
                  {t.description ? (
                    <div className="mt-1 text-xs text-muted-foreground">
                      {t.description.length > 200 ? `${t.description.slice(0, 197)}…` : t.description}
                    </div>
                  ) : null}
                </TableCell>
                <TableCell>{labelSource(t.source)}</TableCell>
                <TableCell className="whitespace-normal">{labelTransport(t.transport)}</TableCell>
                <TableCell>{labelOriginTier(t.origin_tier)}</TableCell>
                <TableCell className="whitespace-normal">
                  {t.available ? (
                    <Badge variant="secondary" aria-label="Tool status: available">
                      Available
                    </Badge>
                  ) : (
                    <Badge variant="outline" aria-label="Tool status: unavailable">
                      Unavailable
                    </Badge>
                  )}
                  {t.requires_credentials ? (
                    <div className="mt-1 text-xs text-muted-foreground">Needs credentials</div>
                  ) : null}
                </TableCell>
                <TableCell className="text-xs whitespace-normal">
                  {t.why_not_available ? <div>{t.why_not_available}</div> : null}
                  {t.capability_id ? (
                    <div>
                      <span className="text-muted-foreground">Service: </span>
                      {t.capability_id}
                    </div>
                  ) : null}
                  {t.connector ? (
                    <div>
                      <span className="text-muted-foreground">Connector: </span>
                      {t.connector}
                    </div>
                  ) : null}
                  {t.action_type ? (
                    <div>
                      <span className="text-muted-foreground">Action: </span>
                      {t.action_type}
                    </div>
                  ) : null}
                  <div className="text-muted-foreground">
                    Permissions: {t.permission_mode === "unknown" ? "Not resolved" : t.permission_mode}
                  </div>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </div>
  );
}
