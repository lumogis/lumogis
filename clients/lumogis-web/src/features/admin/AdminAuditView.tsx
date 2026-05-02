// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { useAuth, useUser } from "../../auth/AuthProvider";
import { ApiError } from "../../api/client";
import { UserPicker, type UserRow } from "../_shared/UserPicker";
import { RoleGate } from "../_shared/RoleGate";

interface AuditEntryDTO {
  id: number;
  action_name: string;
  connector: string;
  mode: string;
  input_summary: string | null;
  result_summary: string | null;
  reverse_token: string | null;
  reverse_action: unknown;
  executed_at: string | null;
  reversed_at: string | null;
}

interface AuditListResponse {
  audit: AuditEntryDTO[];
}

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
  const { client } = useAuth();
  const u = useUser();
  const isAdmin = u?.role === "admin";
  const qc = useQueryClient();
  const [asUser, setAsUser] = useState<string>("");
  const [limit, setLimit] = useState(50);
  const [connector, setConnector] = useState("");
  const [actionType, setActionType] = useState("");
  const [msg, setMsg] = useState<string | null>(null);

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
  const asLabel = useMemo(() => {
    if (!asUser) return "Self (no as_user filter)";
    return usersQ.data?.find((x) => x.id === asUser)?.email ?? asUser;
  }, [asUser, usersQ.data]);

  return (
    <section className="lumogis-admin-dense-section">
      <h2>Audit</h2>
      {msg && <p role="status">{msg}</p>}
      <div className="lumogis-dense-form-grid">
        <label>
          Limit (1–200)
          <input
            type="number"
            min={1}
            max={200}
            value={limit}
            onChange={(e) => setLimit(Number(e.target.value) || 50)}
          />
        </label>
        <label>
          Connector filter
          <input value={connector} onChange={(e) => setConnector(e.target.value)} />
        </label>
        <label>
          Action type filter
          <input value={actionType} onChange={(e) => setActionType(e.target.value)} />
        </label>
        <RoleGate role="admin">
          <div>
            <p style={{ fontSize: "0.9rem" }}>View as user (admin). Current: {asLabel}</p>
            <UserPicker value={asUser} onChange={setAsUser} isAdmin={isAdmin} />
          </div>
        </RoleGate>
        <button type="button" onClick={() => void listQ.refetch()}>
          Refresh
        </button>
      </div>
      {listQ.isPending && <p>Loading…</p>}
      {listQ.isError && <p>Failed to load audit log.</p>}
      {listQ.isSuccess && (
        <div className="lumogis-table-scroll">
          <table className="lumogis-dense-table">
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
              {listQ.data.audit.map((row) => {
                const canReverse =
                  row.reverse_action != null && (row.reversed_at == null || row.reversed_at === "");
                return (
                  <tr key={row.id}>
                    <td style={{ fontSize: "0.8rem" }}>{row.executed_at ?? "—"}</td>
                    <td>{row.action_name}</td>
                    <td>{row.connector}</td>
                    <td>{row.mode}</td>
                    <td className="lumogis-long-text">{row.result_summary}</td>
                    <td>
                      {canReverse && row.reverse_token ? (
                        <button
                          type="button"
                          onClick={() => {
                            setMsg(null);
                            revM.mutate(row.reverse_token!);
                          }}
                        >
                          Reverse
                        </button>
                      ) : null}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
