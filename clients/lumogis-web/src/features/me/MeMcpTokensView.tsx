// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useAuth } from "../../auth/AuthProvider";
import { ApiError } from "../../api/client";
import { CopyOnceModal } from "../_shared/CopyOnceModal";

interface McpRow {
  id: string;
  label: string;
  created_at: string;
}
interface MintRes {
  plaintext: string;
  token: McpRow;
}

export function MeMcpTokensView(): JSX.Element {
  const { client } = useAuth();
  const qc = useQueryClient();
  const [label, setLabel] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [plain, setPlain] = useState<string | null>(null);
  const [showModal, setShowModal] = useState(false);

  const listQ = useQuery({
    queryKey: ["mcp", "me"],
    queryFn: () => client.getJson<McpRow[]>("/api/v1/me/mcp-tokens"),
  });

  const mintM = useMutation({
    mutationFn: () => client.postJson<{ label: string }, MintRes>("/api/v1/me/mcp-tokens", { label: label.trim() }),
    onSuccess: (data) => {
      setPlain(data.plaintext);
      setShowModal(true);
      setLabel("");
      void qc.invalidateQueries({ queryKey: ["mcp", "me"] });
    },
    onError: (e) => {
      setErr(e instanceof ApiError ? e.detail : "Mint failed");
    },
  });

  const delM = useMutation({
    mutationFn: (id: string) => client.delete(`/api/v1/me/mcp-tokens/${encodeURIComponent(id)}`),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["mcp", "me"] }),
  });

  if (listQ.isPending) return <p>Loading…</p>;
  if (listQ.isError) return <p>Failed to load tokens.</p>;

  return (
    <section>
      <h2>MCP tokens</h2>
      {err && <p role="alert">{err}</p>}
      <div style={{ display: "flex", gap: "0.5rem", marginBottom: "1rem" }}>
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
      <ul>
        {listQ.data?.map((t) => (
          <li key={t.id} style={{ marginBottom: "0.5rem" }}>
            {t.label}{" "}
            <code style={{ fontSize: "0.8rem" }}>{t.id}</code>{" "}
            <button type="button" onClick={() => delM.mutate(t.id)}>
              Revoke
            </button>
          </li>
        ))}
      </ul>
      {plain !== null && (
        <CopyOnceModal
          open={showModal}
          title="New MCP token"
          plaintext={plain}
          onClose={() => {
            setShowModal(false);
            setPlain(null);
          }}
        />
      )}
    </section>
  );
}
