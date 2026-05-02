// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Read-only “Tools & capabilities” — GET /api/v1/me/tools. No execution.

import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { ApiError } from "../../api/client";
import { fetchMeTools } from "../../api/meTools";
import { useAuth } from "../../auth/AuthProvider";

const SOURCE_LABELS: Record<string, string> = {
  core: "Core",
  plugin: "Plugin",
  mcp: "MCP",
  proxy: "Proxy",
  capability: "Capability",
  action: "Action",
};

const TRANSPORT_LABELS: Record<string, string> = {
  llm_loop: "Chat / assistant",
  mcp_surface: "MCP clients",
  catalog_only: "Listed only",
  both: "Multiple surfaces",
};

function labelSource(raw: string): string {
  return SOURCE_LABELS[raw] ?? raw;
}

function labelTransport(raw: string): string {
  return TRANSPORT_LABELS[raw] ?? raw.replace(/_/g, " ");
}

function labelOriginTier(raw: string): string {
  return raw.replace(/_/g, " ");
}

export function MeToolsCapabilitiesView(): JSX.Element {
  const { client } = useAuth();
  const [sourceFilter, setSourceFilter] = useState<string>("all");
  const [availFilter, setAvailFilter] = useState<string>("all");

  const q = useQuery({
    queryKey: ["me", "tools"],
    queryFn: () => fetchMeTools(client),
  });

  const filtered = useMemo(() => {
    const tools = q.data?.tools ?? [];
    return tools.filter((t) => {
      if (sourceFilter !== "all" && t.source !== sourceFilter) return false;
      if (availFilter === "available" && !t.available) return false;
      if (availFilter === "unavailable" && t.available) return false;
      return true;
    });
  }, [q.data?.tools, sourceFilter, availFilter]);

  if (q.isPending) {
    return (
      <section aria-busy="true">
        <h2>Tools &amp; capabilities</h2>
        <p>Loading…</p>
      </section>
    );
  }

  if (q.isError) {
    const err = q.error;
    const detail =
      err instanceof ApiError
        ? err.status === 401
          ? "Your session expired or you are not signed in. Refresh the page or sign in again."
          : err.detail
        : "Something went wrong loading the catalog.";
    return (
      <section>
        <h2>Tools &amp; capabilities</h2>
        <p role="alert">{detail}</p>
      </section>
    );
  }

  const { summary } = q.data!;
  const sourceKeys = Object.keys(summary.by_source).sort();

  return (
    <section>
      <h2>Tools &amp; capabilities</h2>
      <p style={{ maxWidth: "42rem", opacity: 0.9 }}>
        What your assistant can use right now — read-only overview. This does not run tools or change
        permissions.
      </p>

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fill, minmax(9rem, 1fr))",
          gap: "0.75rem",
          margin: "1rem 0",
        }}
      >
        <div style={{ border: "1px solid rgba(128,128,128,0.25)", borderRadius: 8, padding: "0.75rem" }}>
          <div style={{ fontSize: "0.8rem", opacity: 0.8 }}>Total</div>
          <div style={{ fontSize: "1.35rem", fontWeight: 700 }} aria-label={`Total tools: ${summary.total}`}>
            {summary.total}
          </div>
        </div>
        <div style={{ border: "1px solid rgba(128,128,128,0.25)", borderRadius: 8, padding: "0.75rem" }}>
          <div style={{ fontSize: "0.8rem", opacity: 0.8 }}>Available</div>
          <div
            style={{ fontSize: "1.35rem", fontWeight: 700 }}
            aria-label={`Available tools: ${summary.available}`}
          >
            {summary.available}
          </div>
        </div>
        <div style={{ border: "1px solid rgba(128,128,128,0.25)", borderRadius: 8, padding: "0.75rem" }}>
          <div style={{ fontSize: "0.8rem", opacity: 0.8 }}>Unavailable</div>
          <div
            style={{ fontSize: "1.35rem", fontWeight: 700 }}
            aria-label={`Unavailable tools: ${summary.unavailable}`}
          >
            {summary.unavailable}
          </div>
        </div>
      </div>

      <div style={{ marginBottom: "0.75rem" }}>
        <strong style={{ fontSize: "0.9rem" }}>By source</strong>
        <ul style={{ margin: "0.35rem 0 0", paddingLeft: "1.2rem" }}>
          {sourceKeys.map((k) => (
            <li key={k}>
              {labelSource(k)}: {summary.by_source[k]}
            </li>
          ))}
        </ul>
      </div>

      <div style={{ display: "flex", flexWrap: "wrap", gap: "1rem", marginBottom: "1rem" }}>
        <label style={{ display: "flex", flexDirection: "column", gap: "0.25rem", fontSize: "0.9rem" }}>
          Source
          <select
            value={sourceFilter}
            onChange={(e) => setSourceFilter(e.target.value)}
            aria-label="Filter by source"
          >
            <option value="all">All</option>
            <option value="core">Core</option>
            <option value="plugin">Plugin</option>
            <option value="mcp">MCP</option>
            <option value="proxy">Proxy</option>
            <option value="capability">Capability</option>
            <option value="action">Action</option>
          </select>
        </label>
        <label style={{ display: "flex", flexDirection: "column", gap: "0.25rem", fontSize: "0.9rem" }}>
          Availability
          <select
            value={availFilter}
            onChange={(e) => setAvailFilter(e.target.value)}
            aria-label="Filter by availability"
          >
            <option value="all">All</option>
            <option value="available">Available</option>
            <option value="unavailable">Unavailable</option>
          </select>
        </label>
      </div>

      {summary.total === 0 ? (
        <p>No tools are listed in the catalog yet.</p>
      ) : filtered.length === 0 ? (
        <p>No tools match the current filters.</p>
      ) : (
        <div style={{ overflowX: "auto" }}>
          <table style={{ borderCollapse: "collapse", width: "100%", fontSize: "0.9rem" }}>
            <thead>
              <tr style={{ textAlign: "left", borderBottom: "1px solid rgba(128,128,128,0.35)" }}>
                <th style={{ padding: "0.5rem 0.35rem" }}>Tool</th>
                <th style={{ padding: "0.5rem 0.35rem" }}>Source</th>
                <th style={{ padding: "0.5rem 0.35rem" }}>Surface</th>
                <th style={{ padding: "0.5rem 0.35rem" }}>Tier</th>
                <th style={{ padding: "0.5rem 0.35rem" }}>Status</th>
                <th style={{ padding: "0.5rem 0.35rem" }}>Details</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((t) => (
                <tr key={`${t.source}-${t.name}-${t.capability_id ?? ""}`} style={{ verticalAlign: "top" }}>
                  <td style={{ padding: "0.5rem 0.35rem" }}>
                    <div style={{ fontWeight: 600 }}>{t.label}</div>
                    <div style={{ fontSize: "0.8rem", opacity: 0.75 }}>{t.name}</div>
                    {t.description ? (
                      <div style={{ fontSize: "0.8rem", marginTop: "0.25rem", opacity: 0.85 }}>
                        {t.description.length > 200 ? `${t.description.slice(0, 197)}…` : t.description}
                      </div>
                    ) : null}
                  </td>
                  <td style={{ padding: "0.5rem 0.35rem" }}>{labelSource(t.source)}</td>
                  <td style={{ padding: "0.5rem 0.35rem" }}>{labelTransport(t.transport)}</td>
                  <td style={{ padding: "0.5rem 0.35rem" }}>{labelOriginTier(t.origin_tier)}</td>
                  <td style={{ padding: "0.5rem 0.35rem" }}>
                    {t.available ? (
                      <span aria-label="Tool status: available">Available</span>
                    ) : (
                      <span
                        aria-label="Tool status: unavailable"
                        style={{ color: "var(--warn, #b45309)" }}
                      >
                        Unavailable
                      </span>
                    )}
                    {t.requires_credentials ? (
                      <div style={{ fontSize: "0.75rem", opacity: 0.8 }}>Needs credentials</div>
                    ) : null}
                  </td>
                  <td style={{ padding: "0.5rem 0.35rem", fontSize: "0.85rem" }}>
                    {t.why_not_available ? <div>{t.why_not_available}</div> : null}
                    {t.capability_id ? (
                      <div style={{ opacity: 0.85 }}>
                        <span style={{ opacity: 0.7 }}>Service: </span>
                        {t.capability_id}
                      </div>
                    ) : null}
                    {t.connector ? (
                      <div style={{ opacity: 0.85 }}>
                        <span style={{ opacity: 0.7 }}>Connector: </span>
                        {t.connector}
                      </div>
                    ) : null}
                    {t.action_type ? (
                      <div style={{ opacity: 0.85 }}>
                        <span style={{ opacity: 0.7 }}>Action: </span>
                        {t.action_type}
                      </div>
                    ) : null}
                    <div style={{ opacity: 0.75 }}>
                      Permissions: {t.permission_mode === "unknown" ? "Not resolved" : t.permission_mode}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
