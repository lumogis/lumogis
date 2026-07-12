// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
import { Fragment } from "react";
import type { AuditEntry } from "../../../api/audit";
import { auditEntryRawPayload, auditEntrySummary } from "../../../util/auditSummary";
import { formatAuditTimestamp } from "../../../util/formatTimestamp";
import { Button } from "../../../components/Button";
import { LoadingPlaceholder, Skeleton } from "../../_shared/Skeleton";
import { ErrorState } from "../../_shared/ErrorState";

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
    const colCount = variant === "admin" ? 6 : 5;
    return (
      <LoadingPlaceholder label="Loading audit log…">
        <div className="lumogis-table-scroll">
          <table className="lumogis-dense-table lumogis-responsive-table">
            <thead>
              <tr>
                <th>When</th>
                <th>{variant === "admin" ? "Action" : "Event"}</th>
                <th>{variant === "admin" ? "Connector" : "Source"}</th>
                {variant === "admin" ? <th>Mode</th> : null}
                <th>{variant === "admin" ? "Result" : "Description"}</th>
                <th></th>
              </tr>
            </thead>
            <tbody data-testid="audit-skeleton">
              {Array.from({ length: 8 }, (_, r) => (
                <tr key={r}>
                  {Array.from({ length: colCount }, (_, c) => (
                    <td key={c}>
                      <Skeleton width={c === colCount - 2 ? "80%" : "55%"} />
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </LoadingPlaceholder>
    );
  }

  if (error) {
    return (
      <ErrorState
        title="Couldn't load the audit log"
        message="Lumogis couldn't fetch recent activity. This is usually temporary."
        onRetry={onRetry}
      />
    );
  }

  if (rows.length === 0) {
    return <p>No audit events match your filters.</p>;
  }

  if (variant === "admin") {
    return (
      <div className="lumogis-table-scroll">
        <table className="lumogis-dense-table lumogis-responsive-table">
          <thead>
            <tr>
              <th>When</th>
              <th>Action</th>
              <th>Connector</th>
              <th>Mode</th>
              <th>Result</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => {
              const canReverse =
                showReverse &&
                row.reverse_action != null &&
                (row.reversed_at == null || row.reversed_at === "");
              return (
                <tr key={row.id}>
                  <td data-label="When" className="lumogis-cell-timestamp">
                    {formatAuditTimestamp(row.executed_at)}
                  </td>
                  <td data-label="Action">{row.action_name}</td>
                  <td data-label="Connector">{row.connector}</td>
                  <td data-label="Mode">{row.mode}</td>
                  <td data-label="Result" className="lumogis-long-text">{auditEntrySummary(row)}</td>
                  <td data-label="">
                    {canReverse && row.reverse_token && onReverse ? (
                      <Button type="button" variant="secondary" size="sm" onClick={() => onReverse(row.reverse_token!)}>
                        Reverse
                      </Button>
                    ) : null}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    );
  }

  return (
    <div className="lumogis-table-scroll">
      <table className="lumogis-dense-table lumogis-responsive-table">
        <thead>
          <tr>
            <th>When</th>
            <th>Event</th>
            <th>Source</th>
            <th>Description</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => {
            const isExpanded = expandedId === row.id;
            const isPrivacy =
              markCloudRows && (row.event_type?.startsWith("privacy.") ?? false);
            const summary = auditEntrySummary(row);
            const rawPayload = auditEntryRawPayload(row);
            return (
              <Fragment key={row.id}>
                <tr>
                  <td data-label="When" className="lumogis-cell-timestamp">
                    {formatAuditTimestamp(row.executed_at)}
                  </td>
                  <td data-label="Event">
                    {row.event_type?.replace(/\./g, " · ") ?? row.action_name}
                    {isPrivacy ? (
                      <span className="lumogis-badge" data-testid="privacy-badge">
                        {" "}
                        Privacy
                      </span>
                    ) : null}
                    {row.reversed_at ? (
                      <span className="lumogis-badge"> Reversed</span>
                    ) : null}
                  </td>
                  <td data-label="Source">{row.source ?? row.connector}</td>
                  <td data-label="Description" className="lumogis-long-text">{summary}</td>
                  <td data-label="">
                    {onToggleExpand ? (
                      <Button type="button" variant="ghost" size="sm" onClick={() => onToggleExpand(row.id)}>
                        {isExpanded ? "Hide" : "Details"}
                      </Button>
                    ) : null}
                  </td>
                </tr>
                {isExpanded ? (
                  <tr>
                    <td colSpan={5}>
                      <dl className="lumogis-audit-detail">
                        <dt>event_type</dt>
                        <dd>{row.event_type}</dd>
                        <dt>action_name</dt>
                        <dd>{row.action_name}</dd>
                        <dt>connector</dt>
                        <dd>{row.connector}</dd>
                        <dt>mode</dt>
                        <dd>{row.mode}</dd>
                        <dt>scope</dt>
                        <dd>{row.scope}</dd>
                        {row.executed_at ? (
                          <>
                            <dt>executed_at (ISO)</dt>
                            <dd className="lumogis-cell-identifier">{row.executed_at}</dd>
                          </>
                        ) : null}
                        {row.description ? (
                          <>
                            <dt>description</dt>
                            <dd className="lumogis-long-text">{row.description}</dd>
                          </>
                        ) : null}
                        {rawPayload ? (
                          <>
                            <dt>raw payload</dt>
                            <dd>
                              <pre className="lumogis-audit-detail__raw">{rawPayload}</pre>
                            </dd>
                          </>
                        ) : null}
                      </dl>
                    </td>
                  </tr>
                ) : null}
              </Fragment>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
