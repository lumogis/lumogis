// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useAuth, useUser } from "../../auth/AuthProvider";
import { ApiError } from "../../api/client";
import { UserPicker } from "../_shared/UserPicker";
import { accessLabel } from "../me/mcpTokenDisplay";

interface McpRow {
  id: string;
  user_id: string;
  label: string;
  created_at: string;
  scopes: string[] | null;
}

function errMsg(e: unknown): string {
  if (e instanceof ApiError) return e.detail;
  return "Request failed";
}

// Admins list + revoke other users' MCP tokens; minting is self-service only
// (each user mints their own at /me/mcp-tokens — LUM-291 D12). The former
// admin Mint button POSTed to a route that never existed (405) and was removed
// in LUM-530.
export function AdminMcpTokensView(): JSX.Element {
  const { client } = useAuth();
  const me = useUser();
  const isAdmin = me?.role === "admin";
  const qc = useQueryClient();
  const [userId, setUserId] = useState("");
  const [err, setErr] = useState<string | null>(null);

  const base = userId
    ? `/api/v1/admin/users/${encodeURIComponent(userId)}/mcp-tokens`
    : null;

  const listQ = useQuery({
    queryKey: ["mcp", "admin", userId],
    queryFn: () => client.getJson<McpRow[]>(base!),
    enabled: Boolean(base),
  });

  const delM = useMutation({
    mutationFn: (id: string) => client.delete(`${base!}/${encodeURIComponent(id)}`),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["mcp", "admin", userId] }),
    onError: (e) => setErr(errMsg(e)),
  });

  return (
    <section className="lumogis-admin-dense-section">
      <h2>MCP tokens (admin)</h2>
      {err && <p role="alert">{err}</p>}
      <UserPicker value={userId} onChange={setUserId} isAdmin={isAdmin} />
      {!userId && <p role="status">Select a user to list their tokens.</p>}
      {userId && listQ.isPending && <p>Loading…</p>}
      {userId && listQ.isError && <p>Failed to load tokens.</p>}
      {userId && listQ.isSuccess && (
        <ul className="lumogis-mcp-token-list">
          {listQ.data?.map((t) => (
            <li key={t.id} style={{ marginBottom: "0.5rem" }}>
              {t.label}{" "}
              <code className="lumogis-long-text" style={{ fontSize: "0.8rem" }}>
                {t.id}
              </code>{" "}
              <span style={{ fontSize: "0.8rem", opacity: 0.8 }}>[{accessLabel(t.scopes)}]</span>{" "}
              <button
                type="button"
                onClick={() => {
                  setErr(null);
                  delM.mutate(t.id);
                }}
              >
                Revoke
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
