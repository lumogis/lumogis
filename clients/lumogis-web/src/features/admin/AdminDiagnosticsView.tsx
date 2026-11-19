// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Admin diagnostics: GET /api/v1/admin/diagnostics + credential-key fingerprint.
// Read-only — no restarts, invokes, or secret display.
//
// Rebuilt on the shared components in LUM-993 plan B Pass 6. A developer page: every
// detail stays visible (all sections open), now in Tables, Badges and one Collapsible
// per section.

import { useQuery } from "@tanstack/react-query";
import { ChevronDown } from "lucide-react";
import type { ReactNode } from "react";

import { ApiError } from "../../api/client";
import {
  fetchAdminDiagnostics,
  type AdminDiagnosticsTask,
  type AdminDiagnosticsTaskSkipReason,
} from "../../api/adminDiagnostics";
import { useAuth } from "../../auth/AuthProvider";
import { ErrorState } from "../../components/page/ErrorState";
import { LoadingPlaceholder, SkeletonText } from "../../components/page/LoadingState";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import { Card, CardContent, CardHeader } from "../../components/ui/card";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "../../components/ui/collapsible";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../../components/ui/table";
import { formatDateTime } from "../../util/formatTimestamp";

type RowsByKey = {
  user: Record<string, number>;
  household: Record<string, number>;
  system: Record<string, number>;
};

const SKIP_LABELS: Record<AdminDiagnosticsTaskSkipReason, string> = {
  unknown: "not configured",
  not_on_box: "not on this network",
  no_tools: "no tool support",
  no_credentials: "no key",
  optional_disabled: "switched off",
  privacy_mode: "blocked by privacy mode",
  error: "check failed",
};

function skipLabel(reason: AdminDiagnosticsTaskSkipReason | null | undefined): string | null {
  return reason ? SKIP_LABELS[reason] : null;
}

function taskRules(t: AdminDiagnosticsTask): string[] {
  const rules: string[] = [];
  if (t.local_only) rules.push("On-box only");
  if (t.needs_tools) rules.push("Needs tools");
  if (!t.threads_user_id) rules.push("Runs without a user");
  if (t.destructive) rules.push("First entry only");
  return rules;
}

function usesNow(t: AdminDiagnosticsTask): string {
  if (t.resolved_model === null) return "Skipped (no on-box model)";
  let text = t.resolved_model;
  if (t.resolved_from === "default") {
    const why = skipLabel(t.default_skip_reason);
    text += why ? ` (default — ${why})` : " (default)";
  } else if (t.destructive) {
    const why = skipLabel(t.configured[0]?.skip_reason);
    if (why) text += ` — ${why}`;
  }
  return text;
}

export function AdminDiagnosticsView(): JSX.Element {
  const { client } = useAuth();

  const summary = useQuery({
    queryKey: ["admin", "diagnostics", "summary"],
    queryFn: () => fetchAdminDiagnostics(client),
  });

  const fingerprint = useQuery({
    queryKey: ["admin", "diagnostics", "fp"],
    queryFn: () =>
      client.getJson<{ current_key_version: number; rows_by_key_version: RowsByKey }>(
        "/api/v1/admin/diagnostics/credential-key-fingerprint",
      ),
  });

/** One diagnostics section: open by default, can be folded away. keepMounted keeps it in the page while closed. */
function DiagSection({ title, children }: { title: string; children: ReactNode }): JSX.Element {
  return (
    <Collapsible defaultOpen>
      <Card className="mb-4 max-w-4xl">
        <CardHeader>
          <h2 className="m-0 text-base font-semibold">
            <CollapsibleTrigger
              render={<Button type="button" variant="ghost" size="sm" className="group/trigger -ml-2 text-base" />}
            >
              <ChevronDown
                aria-hidden="true"
                className="transition-transform group-aria-expanded/trigger:rotate-180"
              />
              {title}
            </CollapsibleTrigger>
          </h2>
        </CardHeader>
        <CollapsibleContent keepMounted>
          <CardContent className="flex flex-col gap-2 text-sm">{children}</CardContent>
        </CollapsibleContent>
      </Card>
    </Collapsible>
  );
}

function Facts({ children }: { children: ReactNode }): JSX.Element {
  return <ul className="m-0 flex list-disc flex-col gap-1 pl-5">{children}</ul>;
}

function Note({ children, ...rest }: { children: ReactNode; role?: string }): JSX.Element {
  return (
    <p className="m-0 max-w-2xl text-xs text-muted-foreground" {...rest}>
      {children}
    </p>
  );
}

  if (summary.isPending) {
    return (
      <LoadingPlaceholder label="Loading diagnostics…" className="max-w-4xl">
        <SkeletonText lines={8} />
      </LoadingPlaceholder>
    );
  }

  if (summary.isError) {
    const err = summary.error;
    const detail =
      err instanceof ApiError
        ? err.status === 401
          ? "Not signed in or session expired."
          : err.status === 403
            ? "Admin role required for instance diagnostics."
            : err.detail
        : "Diagnostics unavailable.";
    return <ErrorState message={detail} onRetry={() => void summary.refetch()} />;
  }

  const d = summary.data;
  const tiers: (keyof RowsByKey)[] = ["user", "household", "system"];
  const tableRows: { tier: string; keyVersion: string; count: number }[] = [];

  if (fingerprint.data) {
    const { rows_by_key_version } = fingerprint.data;
    for (const tier of tiers) {
      const inner = rows_by_key_version[tier] ?? {};
      const keys = Object.keys(inner);
      if (keys.length === 0) {
        tableRows.push({ tier, keyVersion: "—", count: 0 });
      } else {
        for (const kv of keys.sort()) {
          tableRows.push({ tier, keyVersion: kv, count: inner[kv]! });
        }
      }
    }
  }

  return (
    <div>
      <div className="mb-4 flex flex-col gap-1 text-sm text-muted-foreground">
        <div aria-label={`Overall status: ${d.status}`}>
          Overall: <Badge variant={d.status === "ok" ? "secondary" : "destructive"}>{d.status}</Badge>
        </div>
        <div>Generated: {formatDateTime(d.generated_at)}</div>
      </div>

      <DiagSection title="Core">
        <Facts>
          <li>Auth enabled: {d.core.auth_enabled ? "yes" : "no"}</li>
          <li>Tool catalog flag: {d.core.tool_catalog_enabled ? "on" : "off"}</li>
          <li>Core version: {d.core.core_version}</li>
          <li>MCP enabled: {d.core.mcp_enabled ? "yes" : "no"}</li>
          <li>MCP auth required: {d.core.mcp_auth_required ? "yes" : "no"}</li>
        </Facts>
      </DiagSection>

      <DiagSection title="Stores">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Name</TableHead>
              <TableHead>Status</TableHead>
              <TableHead>Note</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {d.stores.map((s) => (
              <TableRow key={s.name}>
                <TableCell className="font-mono text-xs break-all whitespace-normal">{s.name}</TableCell>
                <TableCell>{s.status}</TableCell>
                <TableCell className="text-xs whitespace-normal">{s.message ?? "—"}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </DiagSection>

      <DiagSection title="Capabilities">
        <p className="m-0">
          Registered: {d.capabilities.total} — healthy: {d.capabilities.healthy}, unhealthy:{" "}
          {d.capabilities.unhealthy}
        </p>
        {d.capabilities.services.length > 0 ? (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Id</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Version</TableHead>
                <TableHead>Tools</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {d.capabilities.services.map((s) => (
                <TableRow key={s.id}>
                  <TableCell className="font-mono text-xs break-all whitespace-normal">{s.id}</TableCell>
                  <TableCell>{s.status}</TableCell>
                  <TableCell>{s.version}</TableCell>
                  <TableCell>{s.tools}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        ) : null}
      </DiagSection>

      <DiagSection title="Tool catalog">
        <p className="m-0" aria-label={`Tool catalog total: ${d.tools.total}`}>
          Total: {d.tools.total} — available: {d.tools.available}, unavailable: {d.tools.unavailable}
        </p>
        <Facts>
          {Object.entries(d.tools.by_source)
            .sort(([a], [b]) => a.localeCompare(b))
            .map(([k, n]) => (
              <li key={k}>
                {k}: {n}
              </li>
            ))}
        </Facts>
      </DiagSection>

      <DiagSection title="Tool & permission sanity">
        {d.foundation_signals ? (
          <>
            <Note>
              Read-only catalog, Ask/Do probe, and capability registry mirrors (ADR&nbsp;034). No behaviour changes.
            </Note>
            <Facts>
              <li>
                Catalog rows (sample admin user): {d.foundation_signals.tool_catalog.total_entries}; unavailable
                capability rows: {d.foundation_signals.tool_catalog.unavailable_capability_catalog_entries};
                catalog-only transport rows: {d.foundation_signals.tool_catalog.catalog_only_transport_entries}
              </li>
              <li>
                Permissions module:{" "}
                {d.foundation_signals.permissions.ask_do_module_import_ok ? "import ok" : "import failed"};
                connector-mode probe:{" "}
                {d.foundation_signals.permissions.connector_mode_metadata_lookup_ok
                  ? "ok"
                  : "failed (check Postgres)"}
              </li>
              <li>
                Connector-linked catalog rows still showing unknown permission_mode:{" "}
                {d.foundation_signals.permissions.catalog_rows_with_connector_but_unknown_permission_mode}
              </li>
              <li>
                Capability registry (same as summary above):{" "}
                {d.foundation_signals.capability_registry.registered_services_total} registered, unhealthy{" "}
                {d.foundation_signals.capability_registry.registered_services_unhealthy}
              </li>
            </Facts>
          </>
        ) : (
          <Note role="status">
            Tool and permission sanity slice not present — Core may be older than this Web build. Other diagnostics
            still apply.
          </Note>
        )}
      </DiagSection>

      {d.tasks && d.tasks.length > 0 ? (
        <DiagSection title="Background model tasks">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Task</TableHead>
                <TableHead>Rules</TableHead>
                <TableHead>Configured</TableHead>
                <TableHead>Uses now (for you)</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {d.tasks.map((t) => (
                <TableRow key={t.task} className="align-top">
                  <TableCell className="whitespace-normal">
                    <code className="font-mono text-xs">{t.task}</code>
                    <div className="text-xs text-muted-foreground">{t.description}</div>
                  </TableCell>
                  <TableCell className="whitespace-normal">
                    <span className="flex flex-wrap gap-1">
                      {taskRules(t).map((r) => (
                        <Badge key={r} variant="outline">
                          {r}
                        </Badge>
                      ))}
                    </span>
                  </TableCell>
                  <TableCell className="text-xs whitespace-normal">
                    {t.configured.length === 0
                      ? "—"
                      : t.configured.map((e, i) => {
                          const why = skipLabel(e.skip_reason);
                          return (
                            <div key={`${e.model}-${i}`}>
                              <code className="font-mono">{e.model}</code>
                              {why ? ` (${why})` : ""}
                            </div>
                          );
                        })}
                  </TableCell>
                  <TableCell className="text-xs whitespace-normal">{usesNow(t)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
          <Note>
            Resolved for your account from configuration. Members without the same keys may use a later entry. A task
            marked First entry only ignores any later entries (Core warns at start-up). This is not a health check: a
            listed model that is down still shows here.
          </Note>
        </DiagSection>
      ) : null}

      {d.warnings.length > 0 ? (
        <DiagSection title="Warnings">
          <Facts>
            {d.warnings.map((w) => (
              <li key={w.code}>
                <code className="font-mono">{w.code}</code> — {w.message}
              </li>
            ))}
          </Facts>
        </DiagSection>
      ) : null}

      <DiagSection title="Credential encryption">
        {fingerprint.isPending ? <p className="m-0">Loading key rotation summary…</p> : null}
        {fingerprint.isError ? <p className="m-0">Credential key fingerprint unavailable.</p> : null}
        {fingerprint.data ? (
          <>
            <p className="m-0">Current key version: {fingerprint.data.current_key_version}</p>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Tier</TableHead>
                  <TableHead>Key version</TableHead>
                  <TableHead>Count</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {tableRows.map((r, i) => (
                  <TableRow key={`${r.tier}-${r.keyVersion}-${i}`}>
                    <TableCell>{r.tier}</TableCell>
                    <TableCell>{r.keyVersion}</TableCell>
                    <TableCell>{r.count}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </>
        ) : null}
      </DiagSection>
    </div>
  );
}
