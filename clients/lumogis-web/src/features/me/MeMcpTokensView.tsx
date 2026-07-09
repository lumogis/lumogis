// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useAuth } from "../../auth/AuthProvider";
import { ApiError } from "../../api/client";
import { CopyOnceModal } from "../_shared/CopyOnceModal";
import { accessLabel } from "./mcpTokenDisplay";

interface McpRow {
  id: string;
  label: string;
  created_at: string;
  scopes: string[] | null;
}
interface MintRes {
  plaintext: string;
  token: McpRow;
}

export function MeMcpTokensView(): JSX.Element {
  const { client } = useAuth();
  const qc = useQueryClient();
  const [label, setLabel] = useState("");
  const [writable, setWritable] = useState(false); // LUM-527: default least-privilege
  const [err, setErr] = useState<string | null>(null);
  const [plain, setPlain] = useState<string | null>(null);
  const [showModal, setShowModal] = useState(false);

  const listQ = useQuery({
    queryKey: ["mcp", "me"],
    queryFn: () => client.getJson<McpRow[]>("/api/v1/me/mcp-tokens"),
  });

  const mintM = useMutation({
    mutationFn: () =>
      client.postJson<{ label: string; scopes: string[] }, MintRes>("/api/v1/me/mcp-tokens", {
        label: label.trim(),
        scopes: writable ? ["mcp:read", "mcp:write"] : ["mcp:read"],
      }),
    onSuccess: (data) => {
      setPlain(data.plaintext);
      setShowModal(true);
      setLabel("");
      setWritable(false); // back to least-privilege default for the next mint
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
      <div style={{ display: "flex", gap: "0.5rem", marginBottom: "0.5rem" }}>
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
      <fieldset style={{ border: "none", margin: 0, padding: 0, marginBottom: "1rem" }}>
        <legend style={{ fontSize: "0.85rem", padding: 0 }}>Token access</legend>
        <label style={{ marginRight: "1rem" }}>
          <input
            type="radio"
            name="mcp-token-access"
            checked={!writable}
            onChange={() => setWritable(false)}
          />{" "}
          Read-only
        </label>
        <label>
          <input
            type="radio"
            name="mcp-token-access"
            checked={writable}
            onChange={() => setWritable(true)}
          />{" "}
          Read + write
        </label>
      </fieldset>
      <ul>
        {listQ.data?.map((t) => (
          <li key={t.id} style={{ marginBottom: "0.5rem" }}>
            {t.label}{" "}
            <code style={{ fontSize: "0.8rem" }}>{t.id}</code>{" "}
            <span style={{ fontSize: "0.8rem", opacity: 0.8 }}>[{accessLabel(t.scopes)}]</span>{" "}
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
