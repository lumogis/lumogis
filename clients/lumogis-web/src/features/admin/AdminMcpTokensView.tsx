// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { useAuth, useUser } from "../../auth/AuthProvider";
import { ApiError } from "../../api/client";
import { UserPicker, type UserRow } from "../_shared/UserPicker";
import { CopyOnceModal } from "../_shared/CopyOnceModal";

interface McpRow {
  id: string;
  user_id: string;
  label: string;
  created_at: string;
}

interface MintRes {
  plaintext: string;
  token: McpRow;
}

function errMsg(e: unknown): string {
  if (e instanceof ApiError) return e.detail;
  return "Request failed";
}

export function AdminMcpTokensView(): JSX.Element {
  const { client } = useAuth();
  const me = useUser();
  const isAdmin = me?.role === "admin";
  const qc = useQueryClient();
  const [userId, setUserId] = useState("");
  const [label, setLabel] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [plain, setPlain] = useState<string | null>(null);
  const [showModal, setShowModal] = useState(false);

  const usersQ = useQuery({
    queryKey: ["admin", "users"],
    queryFn: () => client.getJson<UserRow[]>("/api/v1/admin/users"),
    enabled: isAdmin,
  });
  const targetEmail = useMemo(
    () => (userId ? usersQ.data?.find((u) => u.id === userId)?.email : undefined),
    [userId, usersQ.data],
  );

  const base = userId
    ? `/api/v1/admin/users/${encodeURIComponent(userId)}/mcp-tokens`
    : null;

  const listQ = useQuery({
    queryKey: ["mcp", "admin", userId],
    queryFn: () => client.getJson<McpRow[]>(base!),
    enabled: Boolean(base),
  });

  const mintM = useMutation({
    mutationFn: () =>
      client.postJson<{ label: string }, MintRes>(base!, { label: label.trim() }),
    onSuccess: (data) => {
      setPlain(data.plaintext);
      setShowModal(true);
      setLabel("");
      void qc.invalidateQueries({ queryKey: ["mcp", "admin", userId] });
    },
    onError: (e) => {
      setErr(errMsg(e));
    },
  });

  const delM = useMutation({
    mutationFn: (id: string) => client.delete(`${base!}/${encodeURIComponent(id)}`),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["mcp", "admin", userId] }),
  });

  return (
    <section className="lumogis-admin-dense-section">
      <h2>MCP tokens (admin)</h2>
      {err && <p role="alert">{err}</p>}
      <UserPicker value={userId} onChange={setUserId} isAdmin={isAdmin} />
      {!userId && <p role="status">Select a user to list or mint tokens.</p>}
      {userId && listQ.isPending && <p>Loading…</p>}
      {userId && listQ.isError && <p>Failed to load tokens.</p>}
      {userId && listQ.isSuccess && (
        <>
          <div className="lumogis-mcp-mint-row">
            <input
              value={label}
              onChange={(e) => setLabel(e.target.value)}
              placeholder="label (1–64 chars)"
              minLength={1}
              maxLength={64}
            />
            <button
              type="button"
              onClick={() => {
                setErr(null);
                mintM.mutate();
              }}
            >
              Mint
            </button>
          </div>
          <ul className="lumogis-mcp-token-list">
            {listQ.data?.map((t) => (
              <li key={t.id} style={{ marginBottom: "0.5rem" }}>
                {t.label}{" "}
                <code className="lumogis-long-text" style={{ fontSize: "0.8rem" }}>
                  {t.id}
                </code>{" "}
                <button type="button" onClick={() => delM.mutate(t.id)}>
                  Revoke
                </button>
              </li>
            ))}
          </ul>
        </>
      )}
      {plain !== null && (
        <CopyOnceModal
          open={showModal}
          title="New MCP token"
          plaintext={plain}
          onClose={() => {
            setShowModal(false);
            setPlain(null);
          }}
          extraBanner={
            targetEmail ? (
              <p
                style={{
                  background: "rgba(255, 200, 100, 0.15)",
                  padding: "0.5rem",
                  borderRadius: 4,
                  fontSize: "0.9rem",
                }}
              >
                This token is for {targetEmail}. Deliver it via a secure out-of-band channel; do not paste it into chat.
              </p>
            ) : null
          }
        />
      )}
    </section>
  );
}
