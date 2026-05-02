// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useAuth } from "../../auth/AuthProvider";
import { ApiError } from "../../api/client";

interface PermRow {
  user_id: string;
  email: string | null;
  connector: string;
  mode: "ASK" | "DO";
  is_default: boolean;
  updated_at: string | null;
}

function errMsg(e: unknown): string {
  if (e instanceof ApiError) return e.detail;
  return "Request failed";
}

export function AdminConnectorPermissionsView(): JSX.Element {
  const { client } = useAuth();
  const qc = useQueryClient();
  const [msg, setMsg] = useState<string | null>(null);
  const [localMode, setLocalMode] = useState<Record<string, "ASK" | "DO">>({});

  const listQ = useQuery({
    queryKey: ["admin", "permissions", "all"],
    queryFn: () => client.getJson<PermRow[]>("/api/v1/admin/permissions"),
  });

  const rowKey = (r: PermRow) => `${r.user_id}:${r.connector}`;

  const putM = useMutation({
    mutationFn: ({ user_id, connector, mode }: { user_id: string; connector: string; mode: "ASK" | "DO" }) =>
      client.putJson<{ mode: "ASK" | "DO" }, PermRow>(
        `/api/v1/admin/users/${encodeURIComponent(user_id)}/permissions/${encodeURIComponent(connector)}`,
        { mode },
      ),
    onSuccess: () => {
      setMsg("Saved.");
      void qc.invalidateQueries({ queryKey: ["admin", "permissions", "all"] });
    },
    onError: (e) => setMsg(errMsg(e)),
  });

  const delM = useMutation({
    mutationFn: ({ user_id, connector }: { user_id: string; connector: string }) =>
      client.delete<PermRow>(
        `/api/v1/admin/users/${encodeURIComponent(user_id)}/permissions/${encodeURIComponent(connector)}`,
      ),
    onSuccess: () => {
      setMsg("Reverted to default.");
      void qc.invalidateQueries({ queryKey: ["admin", "permissions", "all"] });
    },
    onError: (e) => setMsg(errMsg(e)),
  });

  if (listQ.isPending) return <p>Loading…</p>;
  if (listQ.isError) return <p>Failed to load permissions.</p>;

  return (
    <section>
      <h2>Connector permissions (explicit rows)</h2>
      {msg && <p role="status">{msg}</p>}
      <p style={{ fontSize: "0.9rem" }}>
        Only per-user overrides are listed. Connectors using the household default (ASK) without an explicit row do not
        appear here.
      </p>
      <table>
        <thead>
          <tr>
            <th>User</th>
            <th>Connector</th>
            <th>Mode</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {listQ.data?.map((r) => {
            const k = rowKey(r);
            const mode = localMode[k] ?? r.mode;
            return (
              <tr key={k}>
                <td>{r.email ?? r.user_id}</td>
                <td>{r.connector}</td>
                <td>
                  <select
                    aria-label={`Mode for ${r.connector}`}
                    value={mode}
                    onChange={(e) => {
                      const m = e.target.value as "ASK" | "DO";
                      setLocalMode((s) => ({ ...s, [k]: m }));
                    }}
                  >
                    <option value="ASK">ASK</option>
                    <option value="DO">DO</option>
                  </select>
                </td>
                <td>
                  <button
                    type="button"
                    onClick={() => {
                      setMsg(null);
                      putM.mutate({ user_id: r.user_id, connector: r.connector, mode: localMode[k] ?? r.mode });
                    }}
                  >
                    Save
                  </button>{" "}
                  <button
                    type="button"
                    onClick={() => {
                      setMsg(null);
                      delM.mutate({ user_id: r.user_id, connector: r.connector });
                    }}
                  >
                    Revert
                  </button>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </section>
  );
}
