// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Read-only LLM providers — GET /api/v1/me/llm-providers. No editing.

import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { ApiError } from "../../api/client";
import { fetchMeLlmProviders } from "../../api/meLlmProviders";
import { useAuth } from "../../auth/AuthProvider";

const TIER_LABELS: Record<string, string> = {
  user: "Your account",
  household: "Household shared",
  system: "Instance / system",
  env: "Environment fallback",
  none: "None",
};

function labelTier(raw: string): string {
  return TIER_LABELS[raw] ?? raw.replace(/_/g, " ");
}

export function MeLlmProvidersView(): JSX.Element {
  const { client } = useAuth();
  const [tierFilter, setTierFilter] = useState<string>("all");

  const q = useQuery({
    queryKey: ["me", "llm-providers"],
    queryFn: () => fetchMeLlmProviders(client),
  });

  const filtered = useMemo(() => {
    const providers = q.data?.providers ?? [];
    if (tierFilter === "all") return providers;
    if (tierFilter === "configured") return providers.filter((p) => p.configured);
    if (tierFilter === "not_configured") return providers.filter((p) => !p.configured);
    return providers.filter((p) => p.active_tier === tierFilter);
  }, [q.data?.providers, tierFilter]);

  if (q.isPending) {
    return (
      <section aria-busy="true">
        <h2>LLM providers</h2>
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
        : "Something went wrong loading LLM provider status.";
    return (
      <section>
        <h2>LLM providers</h2>
        <p role="alert">{detail}</p>
      </section>
    );
  }

  const { summary } = q.data!;

  return (
    <section className="lumogis-admin-dense-section">
      <h2>LLM providers</h2>
      <p style={{ maxWidth: "42rem", opacity: 0.9 }}>
        Cloud API credentials for assistant models — read-only overview. This does not show keys or
        edit credentials. Use <strong>Connectors</strong> to add or change keys.
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
          <div style={{ fontSize: "0.8rem", opacity: 0.8 }}>Providers</div>
          <div style={{ fontSize: "1.35rem", fontWeight: 700 }} aria-label={`Total providers: ${summary.total}`}>
            {summary.total}
          </div>
        </div>
        <div style={{ border: "1px solid rgba(128,128,128,0.25)", borderRadius: 8, padding: "0.75rem" }}>
          <div style={{ fontSize: "0.8rem", opacity: 0.8 }}>Configured</div>
          <div
            style={{ fontSize: "1.35rem", fontWeight: 700 }}
            aria-label={`Configured providers: ${summary.configured}`}
          >
            {summary.configured}
          </div>
        </div>
        <div style={{ border: "1px solid rgba(128,128,128,0.25)", borderRadius: 8, padding: "0.75rem" }}>
          <div style={{ fontSize: "0.8rem", opacity: 0.8 }}>Not configured</div>
          <div
            style={{ fontSize: "1.35rem", fontWeight: 700 }}
            aria-label={`Not configured: ${summary.not_configured}`}
          >
            {summary.not_configured}
          </div>
        </div>
      </div>

      <div style={{ marginBottom: "0.75rem" }}>
        <strong style={{ fontSize: "0.9rem" }}>By active tier</strong>
        <ul style={{ margin: "0.35rem 0 0", paddingLeft: "1.2rem" }}>
          {Object.entries(summary.by_active_tier)
            .sort(([a], [b]) => a.localeCompare(b))
            .map(([k, n]) => (
              <li key={k}>
                {labelTier(k)}: {n}
              </li>
            ))}
        </ul>
      </div>

      <div style={{ marginBottom: "1rem" }}>
        <label style={{ display: "flex", flexDirection: "column", gap: "0.25rem", fontSize: "0.9rem" }}>
          Filter
          <select
            value={tierFilter}
            onChange={(e) => setTierFilter(e.target.value)}
            aria-label="Filter providers"
          >
            <option value="all">All</option>
            <option value="configured">Configured only</option>
            <option value="not_configured">Not configured</option>
            <option value="user">Active tier: your account</option>
            <option value="household">Active tier: household</option>
            <option value="system">Active tier: system</option>
            <option value="env">Active tier: env fallback</option>
            <option value="none">Active tier: none</option>
          </select>
        </label>
      </div>

      {summary.configured === 0 ? (
        <p>No cloud LLM credentials are configured yet. Use Connectors to add API keys (when signed in).</p>
      ) : null}

      <div className="lumogis-table-scroll">
        <table className="lumogis-dense-table">
          <thead>
            <tr>
              <th>Provider</th>
              <th>Connector</th>
              <th>Status</th>
              <th>Active tier</th>
              <th>Details</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((p) => (
              <tr key={p.connector} style={{ verticalAlign: "top" }}>
                <td style={{ padding: "0.5rem 0.35rem" }}>
                  <div style={{ fontWeight: 600 }}>{p.label}</div>
                  {p.description ? (
                    <div style={{ fontSize: "0.8rem", marginTop: "0.25rem", opacity: 0.85 }}>
                      {p.description.length > 220 ? `${p.description.slice(0, 217)}…` : p.description}
                    </div>
                  ) : null}
                </td>
                <td style={{ fontFamily: "monospace", fontSize: "0.8rem" }} className="lumogis-long-text">
                  {p.connector}
                </td>
                <td style={{ padding: "0.5rem 0.35rem" }}>
                  {p.configured ? (
                    <span aria-label={`${p.label} configured`}>Configured</span>
                  ) : (
                    <span style={{ color: "var(--warn, #b45309)" }} aria-label={`${p.label} not configured`}>
                      Not configured
                    </span>
                  )}
                </td>
                <td style={{ padding: "0.5rem 0.35rem" }}>{labelTier(p.active_tier)}</td>
                <td style={{ padding: "0.5rem 0.35rem", fontSize: "0.85rem" }} className="lumogis-long-text">
                  {p.updated_at ? (
                    <div>
                      <span style={{ opacity: 0.7 }}>Updated: </span>
                      {p.updated_at}
                    </div>
                  ) : null}
                  {p.key_version != null ? (
                    <div>
                      <span style={{ opacity: 0.7 }}>Key version: </span>
                      {p.key_version}
                    </div>
                  ) : null}
                  {p.user_credential_present ? <div>Your credential row present</div> : null}
                  {p.household_credential_available ? <div>Household row present</div> : null}
                  {p.system_credential_available ? <div>System row present</div> : null}
                  {p.env_fallback_available ? <div>Env fallback may apply (single-user / auth off)</div> : null}
                  {p.why_not_available ? <div style={{ opacity: 0.9 }}>{p.why_not_available}</div> : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
