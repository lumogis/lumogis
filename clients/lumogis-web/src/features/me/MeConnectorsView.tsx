// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { useAuth } from "../../auth/AuthProvider";
import { ApiError } from "../../api/client";
import { getCredentialFormComponent } from "../credentials/forms";

interface RegItem {
  id: string;
  description: string;
}
interface CCRow {
  connector: string;
  updated_at: string;
}

export function MeConnectorsView({ llmOnly = false, basePath = "/api/v1/me/connector-credentials" }: { llmOnly?: boolean; basePath?: string }): JSX.Element {
  const { client } = useAuth();
  const qc = useQueryClient();
  const [msg, setMsg] = useState<string | null>(null);

  const regQ = useQuery({
    queryKey: ["cc", "registry"],
    queryFn: () => client.getJson<{ items: RegItem[] }>("/api/v1/me/connector-credentials/registry"),
  });

  const listQ = useQuery({
    queryKey: ["cc", "list", basePath],
    queryFn: () => client.getJson<{ items: CCRow[] }>(basePath),
  });

  const listItems = useMemo(() => listQ.data?.items ?? [], [listQ.data?.items]);
  const existing = useMemo(() => new Set(listItems.map((i) => i.connector)), [listItems]);

  const fromRegistry = useMemo(
    () => (regQ.data?.items ?? []).filter((i) => (llmOnly ? i.id.startsWith("llm_") : true)),
    [regQ.data, llmOnly],
  );

  const items: RegItem[] = useMemo(() => {
    if (regQ.isSuccess) return fromRegistry;
    if (regQ.isError) {
      return listItems
        .filter((i) => (llmOnly ? i.connector.startsWith("llm_") : true))
        .map((i) => ({ id: i.connector, description: "" }));
    }
    return [];
  }, [regQ.isSuccess, regQ.isError, fromRegistry, listItems, llmOnly]);

  const putM = useMutation({
    mutationFn: async ({ connector, payload }: { connector: string; payload: Record<string, unknown> }) => {
      await client.putJson(`${basePath}/${encodeURIComponent(connector)}`, { payload });
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["cc", "list"] });
      setMsg("Saved.");
    },
    onError: (e) => {
      setMsg(e instanceof ApiError ? e.detail : "Save failed");
    },
  });

  const delM = useMutation({
    mutationFn: async (connector: string) => {
      await client.delete(`${basePath}/${encodeURIComponent(connector)}`);
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["cc", "list"] });
      setMsg("Deleted.");
    },
    onError: (e) => {
      setMsg(e instanceof ApiError ? e.detail : "Delete failed");
    },
  });

  if (listQ.isPending) return <p>Loading…</p>;
  if (regQ.isPending && !regQ.isError) return <p>Loading…</p>;

  return (
    <section>
      <h2>{llmOnly ? "LLM providers" : "Connectors"}</h2>
      {regQ.isError && (
        <p role="status">Connector schema hints unavailable; using JSON fallback for listed connectors.</p>
      )}
      {msg && <p role="status">{msg}</p>}
      {items.map((item) => (
        <ConnectorRow
          key={item.id}
          connector={item.id}
          description={item.description}
          isEdit={existing.has(item.id)}
          onSave={(payload) => {
            setMsg(null);
            putM.mutate({ connector: item.id, payload });
          }}
          onDelete={() => {
            if (existing.has(item.id) && window.confirm(`Delete ${item.id}?`)) {
              setMsg(null);
              delM.mutate(item.id);
            }
          }}
        />
      ))}
    </section>
  );
}

function ConnectorRow({
  connector,
  description,
  isEdit,
  onSave,
  onDelete,
}: {
  connector: string;
  description: string;
  isEdit: boolean;
  onSave: (p: Record<string, unknown>) => void;
  onDelete: () => void;
}): JSX.Element {
  const Form = getCredentialFormComponent(connector);
  return (
    <article style={{ border: "1px solid #444", borderRadius: 6, padding: "0.75rem", marginBottom: "0.75rem" }}>
      <h3 style={{ marginTop: 0 }}>
        {connector}
        {isEdit && <span style={{ fontSize: "0.75rem", marginLeft: "0.5rem", opacity: 0.7 }}>(configured)</span>}
      </h3>
      <p style={{ fontSize: "0.85rem" }}>{description}</p>
      <Form
        value={{}}
        isEdit={isEdit}
        onChange={onSave}
        hint="Payload is sent encrypted to the server; use typed fields where available."
      />
      {isEdit && (
        <button type="button" onClick={onDelete} style={{ marginTop: "0.5rem" }}>
          Delete
        </button>
      )}
    </article>
  );
}
