// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useAuth } from "../../auth/AuthProvider";

interface PermRow {
  connector: string;
  mode: "ASK" | "DO";
  is_default: boolean;
  updated_at: string | null;
}

export function MePermissionsView(): JSX.Element {
  const { client } = useAuth();
  const qc = useQueryClient();

  const q = useQuery({
    queryKey: ["me", "permissions"],
    queryFn: () => client.getJson<PermRow[]>("/api/v1/me/permissions"),
  });

  const putM = useMutation({
    mutationFn: async ({ connector, mode }: { connector: string; mode: "ASK" | "DO" }) => {
      await client.putJson(`/api/v1/me/permissions/${encodeURIComponent(connector)}`, { mode });
    },
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["me", "permissions"] }),
  });

  const delM = useMutation({
    mutationFn: (connector: string) => client.delete(`/api/v1/me/permissions/${encodeURIComponent(connector)}`),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["me", "permissions"] }),
  });

  if (q.isPending) return <p>Loading…</p>;
  if (q.isError) return <p>Failed to load permissions.</p>;

  return (
    <section>
      <h2>Permissions</h2>
      <table>
        <thead>
          <tr>
            <th>Connector</th>
            <th>Mode</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {q.data?.map((r) => (
            <tr key={r.connector}>
              <td>
                {r.connector}{" "}
                {r.is_default && <span style={{ opacity: 0.6, fontSize: "0.8rem" }}>(default ASK)</span>}
              </td>
              <td>
                <select
                  value={r.mode}
                  onChange={(e) => {
                    const mode = e.target.value as "ASK" | "DO";
                    putM.mutate({ connector: r.connector, mode });
                  }}
                >
                  <option value="ASK">ASK</option>
                  <option value="DO">DO</option>
                </select>
              </td>
              <td>
                {!r.is_default && (
                  <button type="button" onClick={() => delM.mutate(r.connector)}>
                    Revert
                  </button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
