// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// The activity table, on the shared components (LUM-993 plan B Pass 6). One
// table for both views; on a phone each row stacks (the header hides, the
// sentence takes the full width and the time sits beside it). "What happened"
// is a plain sentence (auditWords); the raw codes live in the row's Details.
import { Fragment } from "react";

import type { AuditEntry } from "../../../api/audit";
import { EmptyState } from "../../../components/page/EmptyState";
import { ErrorState } from "../../../components/page/ErrorState";
import { LoadingPlaceholder, SkeletonText } from "../../../components/page/LoadingState";
import { Badge } from "../../../components/ui/badge";
import { Button } from "../../../components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../../../components/ui/table";
import { auditEntryRawPayload, auditEntrySummary } from "../../../util/auditSummary";
import { formatDateTime } from "../../../util/formatTimestamp";
import { auditSentence } from "./auditWords";

export interface AuditTableProps {
  rows: AuditEntry[];
  loading: boolean;
  error: boolean;
  onRetry?: () => void;
  expandedId?: number | null;
  onToggleExpand?: (id: number) => void;
  showReverse?: boolean;
  onReverse?: (token: string) => void;
  markCloudRows?: boolean;
  variant?: "admin" | "member";
}

const ROW = "max-sm:flex max-sm:flex-wrap max-sm:items-center max-sm:gap-x-3";

export function AuditTable({
  rows,
  loading,
  error,
  onRetry,
  expandedId = null,
  onToggleExpand,
  showReverse = false,
  onReverse,
  markCloudRows = false,
  variant = "member",
}: AuditTableProps): JSX.Element {
  if (loading) {
    return (
      <LoadingPlaceholder label="Loading activity…">
        <div data-testid="audit-skeleton">
          <SkeletonText lines={8} />
        </div>
      </LoadingPlaceholder>
    );
  }

  if (error) {
    return (
      <ErrorState
        title="Couldn't load the activity"
        message="Lumogis couldn't fetch recent activity. This is usually temporary."
        onRetry={onRetry}
      />
    );
  }

  if (rows.length === 0) {
    return (
      <EmptyState
        title="Nothing to show"
        helperText="No audit events match your filters. Try a longer date range or choose All."
      />
    );
  }

  const isAdmin = variant === "admin";

  return (
    <Table>
      <TableHeader className="max-sm:hidden">
        <TableRow>
          <TableHead>When</TableHead>
          <TableHead>What happened</TableHead>
          <TableHead>{isAdmin ? "Result" : "Details"}</TableHead>
          <TableHead className="w-px">
            <span className="sr-only">Actions</span>
          </TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {rows.map((row) => {
          const isExpanded = expandedId === row.id;
          const isPrivacy = markCloudRows && (row.event_type?.startsWith("privacy.") ?? false);
          const summary = auditEntrySummary(row);
          const rawPayload = auditEntryRawPayload(row);
          const canReverse =
            showReverse &&
            row.reverse_action != null &&
            (row.reversed_at == null || row.reversed_at === "");
          return (
            <Fragment key={row.id}>
              <TableRow className={ROW}>
                <TableCell className="whitespace-nowrap text-muted-foreground max-sm:order-2">
                  {formatDateTime(row.executed_at)}
                </TableCell>
                <TableCell className="whitespace-normal max-sm:order-1 max-sm:w-full max-sm:font-medium sm:max-w-64">
                  {auditSentence(row)}
                  {isPrivacy ? (
                    <Badge variant="secondary" className="ml-2" data-testid="privacy-badge">
                      Privacy
                    </Badge>
                  ) : null}
                  {row.reversed_at ? (
                    <Badge variant="outline" className="ml-2">
                      Reversed
                    </Badge>
                  ) : null}
                </TableCell>
                <TableCell className="whitespace-normal break-words text-muted-foreground max-sm:order-3 max-sm:w-full sm:max-w-80">
                  {summary}
                </TableCell>
                <TableCell className="whitespace-nowrap text-right max-sm:order-4">
                  <div className="flex items-center justify-end gap-1">
                    {canReverse && row.reverse_token && onReverse ? (
                      <Button type="button" variant="outline" size="sm" onClick={() => onReverse(row.reverse_token!)}>
                        Reverse
                      </Button>
                    ) : null}
                    {onToggleExpand ? (
                      <Button
                        type="button"
                        variant="ghost"
                        size="sm"
                        aria-expanded={isExpanded}
                        onClick={() => onToggleExpand(row.id)}
                      >
                        {isExpanded ? "Hide" : "Details"}
                      </Button>
                    ) : null}
                  </div>
                </TableCell>
              </TableRow>
              {isExpanded ? (
                <TableRow className="max-sm:block">
                  <TableCell colSpan={4} className="whitespace-normal max-sm:block">
                    <dl className="m-0 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-xs">
                      <dt className="text-muted-foreground">event_type</dt>
                      <dd className="m-0 break-all">{row.event_type}</dd>
                      <dt className="text-muted-foreground">action_name</dt>
                      <dd className="m-0 break-all">{row.action_name}</dd>
                      <dt className="text-muted-foreground">source</dt>
                      <dd className="m-0 break-all">{row.source ?? row.connector}</dd>
                      <dt className="text-muted-foreground">connector</dt>
                      <dd className="m-0 break-all">{row.connector}</dd>
                      <dt className="text-muted-foreground">mode</dt>
                      <dd className="m-0">{row.mode}</dd>
                      <dt className="text-muted-foreground">scope</dt>
                      <dd className="m-0">{row.scope}</dd>
                      {row.executed_at ? (
                        <>
                          <dt className="text-muted-foreground">executed_at (ISO)</dt>
                          <dd className="m-0 break-all">{row.executed_at}</dd>
                        </>
                      ) : null}
                      {row.description ? (
                        <>
                          <dt className="text-muted-foreground">description</dt>
                          <dd className="m-0 break-words">{row.description}</dd>
                        </>
                      ) : null}
                      {rawPayload ? (
                        <>
                          <dt className="text-muted-foreground">raw payload</dt>
                          <dd className="m-0">
                            <pre className="m-0 max-w-full overflow-x-auto rounded bg-muted p-2 whitespace-pre-wrap break-words">
                              {rawPayload}
                            </pre>
                          </dd>
                        </>
                      ) : null}
                    </dl>
                  </TableCell>
                </TableRow>
              ) : null}
            </Fragment>
          );
        })}
      </TableBody>
    </Table>
  );
}
