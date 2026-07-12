// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { useAuth, useUser } from "../../auth/AuthProvider";
import { ApiError } from "../../api/client";
import type { AuditListResponse } from "../../api/audit";
import { buildAuditStreamUrl, mergeAuditRows } from "../../api/audit";
import type { UserRow } from "../_shared/UserPicker";
import { AuditFilters } from "../audit/AuditFilters";
import { AuditTable } from "../audit/_shared/AuditTable";
import { useAuditLiveTail } from "../audit/useAuditLiveTail";

function parseErrorPayload(e: ApiError): { error?: string; detail?: string } {
  try {
    const o = JSON.parse(e.detail) as { detail?: unknown };
    if (o.detail && typeof o.detail === "object" && o.detail !== null) {
      return o.detail as { error?: string; detail?: string };
    }
    if (typeof o.detail === "string") {
      return { error: o.detail };
    }
  } catch {
    /* keep fallthrough */
  }
  return {};
}

function errMsg(e: unknown): string {
  if (e instanceof ApiError) {
    const p = parseErrorPayload(e);
    if (e.status === 404 && p.error === "unknown_reverse_token") {
      return "Reverse token not found (it may have already been reversed, or it belongs to another user)";
    }
    if (e.status === 400 && p.error === "already_reversed") {
      return "Already reversed.";
    }
    if (e.status === 400 && p.error === "reverse_failed") {
      return `Reverse failed: ${p.detail ?? e.detail}`;
    }
    return e.detail;
  }
  return "Request failed";
}

function shouldInvalidateAfterReverseError(e: unknown): boolean {
  if (!(e instanceof ApiError)) return false;
  const p = parseErrorPayload(e);
  return (
    (e.status === 404 && p.error === "unknown_reverse_token") ||
    (e.status === 400 && p.error === "already_reversed")
  );
}

export function AdminAuditView(): JSX.Element {
  const { client, tokens } = useAuth();
  const u = useUser();
  const isAdmin = u?.role === "admin";
  const qc = useQueryClient();
  const [asUser, setAsUser] = useState<string>("");
  const [limit, setLimit] = useState(50);
  const [connector, setConnector] = useState("");
  const [actionType, setActionType] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const [liveEnabled, setLiveEnabled] = useState(false);

  const auditUrl = useMemo(() => {
    const p = new URLSearchParams();
    p.set("limit", String(limit));
    if (connector.trim()) p.set("connector", connector.trim());
    if (actionType.trim()) p.set("action_type", actionType.trim());
    if (isAdmin && asUser) p.set("as_user", asUser);
    return `/api/v1/audit?${p.toString()}`;
  }, [limit, connector, actionType, asUser, isAdmin]);

  const listQ = useQuery({
    queryKey: ["admin", "audit", auditUrl],
    queryFn: () => client.getJson<AuditListResponse>(auditUrl),
  });

  const baseRows = useMemo(() => listQ.data?.audit ?? [], [listQ.data]);
  const sinceId = baseRows.reduce((max, row) => Math.max(max, row.id), 0);
  const streamUrl = useMemo(
    () =>
      buildAuditStreamUrl({
        sinceId,
        connector,
        actionType,
        asUser: isAdmin && asUser ? asUser : undefined,
      }),
    [sinceId, connector, actionType, asUser, isAdmin],
  );
  const liveRows = useAuditLiveTail({ enabled: liveEnabled, streamUrl, tokens });
  const displayRows = useMemo(() => mergeAuditRows(baseRows, liveRows), [baseRows, liveRows]);

  const revM = useMutation({
    mutationFn: (token: string) => client.postJson<Record<string, never>, { status: string }>(`/api/v1/audit/${encodeURIComponent(token)}/reverse`, {}),
    onSuccess: () => {
      setMsg("Reversed.");
      void qc.invalidateQueries({ queryKey: ["admin", "audit"] });
    },
    onError: (e) => {
      setMsg(errMsg(e));
      if (shouldInvalidateAfterReverseError(e)) {
        void qc.invalidateQueries({ queryKey: ["admin", "audit"] });
      }
    },
  });

  const usersQ = useQuery({
    queryKey: ["admin", "users"],
    queryFn: () => client.getJson<UserRow[]>("/api/v1/admin/users"),
    enabled: isAdmin,
  });

  return (
    <section className="lumogis-admin-dense-section">
      <h2>Household audit</h2>
      {msg && <p role="status">{msg}</p>}
      <AuditFilters
        scope="household"
        liveEnabled={liveEnabled}
        onLiveChange={setLiveEnabled}
        onRefresh={() => void listQ.refetch()}
        limit={limit}
        onLimitChange={setLimit}
        connector={connector}
        onConnectorChange={setConnector}
        actionTypeFilter={actionType}
        onActionTypeFilterChange={setActionType}
        asUser={asUser}
        onAsUserChange={isAdmin ? setAsUser : undefined}
        users={usersQ.data}
        usersLoading={usersQ.isPending}
        usersError={usersQ.isError}
      />
      <AuditTable
        variant="admin"
        rows={displayRows}
        loading={listQ.isPending}
        error={listQ.isError}
        onRetry={() => void listQ.refetch()}
        showReverse
        onReverse={(token) => {
          setMsg(null);
          revM.mutate(token);
        }}
      />
    </section>
  );
}
