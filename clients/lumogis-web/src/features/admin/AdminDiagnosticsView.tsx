// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Admin diagnostics: GET /api/v1/admin/diagnostics + credential-key fingerprint.
// Read-only — no restarts, invokes, or secret display.

import { useQuery } from "@tanstack/react-query";

import { ApiError } from "../../api/client";
import { fetchAdminDiagnostics } from "../../api/adminDiagnostics";
import { useAuth } from "../../auth/AuthProvider";

type RowsByKey = {
  user: Record<string, number>;
  household: Record<string, number>;
  system: Record<string, number>;
};

export function AdminDiagnosticsView(): JSX.Element {
  const { client } = useAuth();

  const summary = useQuery({
    queryKey: ["admin", "diagnostics", "summary"],
    queryFn: () => fetchAdminDiagnostics(client),
  });

  const fingerprint = useQuery({
    queryKey: ["admin", "diagnostics", "fp"],
    queryFn: () =>
      client.getJson<{ current_key_version: number; rows_by_key_version: RowsByKey }>(
        "/api/v1/admin/diagnostics/credential-key-fingerprint",
      ),
  });

  if (summary.isPending) {
    return (
      <section aria-busy="true" data-testid="lumogis-admin-diagnostics">
        <h2>Diagnostics</h2>
        <p>Loading…</p>
      </section>
    );
  }

  if (summary.isError) {
    const err = summary.error;
    const detail =
      err instanceof ApiError
        ? err.status === 401
          ? "Not signed in or session expired."
          : err.status === 403
            ? "Admin role required for instance diagnostics."
            : err.detail
        : "Diagnostics unavailable.";
    return (
      <section data-testid="lumogis-admin-diagnostics">
        <h2>Diagnostics</h2>
        <p role="alert">{detail}</p>
      </section>
    );
  }

  const d = summary.data;
  const tiers: (keyof RowsByKey)[] = ["user", "household", "system"];
  const tableRows: { tier: string; keyVersion: string; count: number }[] = [];

  if (fingerprint.data) {
    const { rows_by_key_version } = fingerprint.data;
    for (const tier of tiers) {
      const inner = rows_by_key_version[tier] ?? {};
      const keys = Object.keys(inner);
      if (keys.length === 0) {
        tableRows.push({ tier, keyVersion: "—", count: 0 });
      } else {
        for (const kv of keys.sort()) {
          tableRows.push({ tier, keyVersion: kv, count: inner[kv]! });
        }
      }
    }
  }

  return (
    <section className="lumogis-admin-dense-section" data-testid="lumogis-admin-diagnostics">
      <h2>Diagnostics</h2>
      <p style={{ maxWidth: "42rem", opacity: 0.9 }}>
        Read-only instance overview for admins. Does not run tools, change configuration, or reveal
        credentials. For raw JSON health counts, open{" "}
        <a href="/health" target="_blank" rel="noopener noreferrer">
          stack health (legacy)
        </a>
        .
      </p>

      <div style={{ margin: "1rem 0" }}>
        <div
          style={{ fontSize: "0.85rem", opacity: 0.85 }}
          aria-label={`Overall status: ${d.status}`}
        >
          Overall: <strong>{d.status}</strong>
        </div>
        <div style={{ fontSize: "0.85rem", opacity: 0.85 }}>Generated: {d.generated_at}</div>
      </div>

      <h3 style={{ fontSize: "1rem", marginTop: "1.25rem" }}>Core</h3>
      <ul style={{ margin: "0.35rem 0", paddingLeft: "1.2rem" }}>
        <li>Auth enabled: {d.core.auth_enabled ? "yes" : "no"}</li>
        <li>Tool catalog flag: {d.core.tool_catalog_enabled ? "on" : "off"}</li>
        <li>Core version: {d.core.core_version}</li>
        <li>MCP enabled: {d.core.mcp_enabled ? "yes" : "no"}</li>
        <li>MCP auth required: {d.core.mcp_auth_required ? "yes" : "no"}</li>
      </ul>

      <h3 style={{ fontSize: "1rem", marginTop: "1.25rem" }}>Stores</h3>
      <div className="lumogis-table-scroll">
        <table className="lumogis-dense-table">
          <thead>
            <tr>
              <th>Name</th>
              <th>Status</th>
              <th>Note</th>
            </tr>
          </thead>
          <tbody>
            {d.stores.map((s) => (
              <tr key={s.name}>
                <td style={{ fontFamily: "monospace", fontSize: "0.85rem" }} className="lumogis-long-text">
                  {s.name}
                </td>
                <td>{s.status}</td>
                <td style={{ fontSize: "0.85rem" }} className="lumogis-long-text">
                  {s.message ?? "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <h3 style={{ fontSize: "1rem", marginTop: "1.25rem" }}>Capabilities</h3>
      <p style={{ fontSize: "0.9rem" }}>
        Registered: {d.capabilities.total} — healthy: {d.capabilities.healthy}, unhealthy:{" "}
        {d.capabilities.unhealthy}
      </p>
      {d.capabilities.services.length > 0 ? (
        <div className="lumogis-table-scroll">
          <table className="lumogis-dense-table">
            <thead>
              <tr>
                <th>Id</th>
                <th>Status</th>
                <th>Version</th>
                <th>Tools</th>
              </tr>
            </thead>
            <tbody>
              {d.capabilities.services.map((s) => (
                <tr key={s.id}>
                  <td style={{ fontFamily: "monospace", fontSize: "0.8rem" }} className="lumogis-long-text">
                    {s.id}
                  </td>
                  <td>{s.status}</td>
                  <td>{s.version}</td>
                  <td>{s.tools}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}

      <h3 style={{ fontSize: "1rem", marginTop: "1.25rem" }}>Tool catalog</h3>
      <p style={{ fontSize: "0.9rem" }} aria-label={`Tool catalog total: ${d.tools.total}`}>
        Total: {d.tools.total} — available: {d.tools.available}, unavailable: {d.tools.unavailable}
      </p>
      <ul style={{ margin: "0.35rem 0", paddingLeft: "1.2rem", fontSize: "0.9rem" }}>
        {Object.entries(d.tools.by_source)
          .sort(([a], [b]) => a.localeCompare(b))
          .map(([k, n]) => (
            <li key={k}>
              {k}: {n}
            </li>
          ))}
      </ul>

      {d.warnings.length > 0 ? (
        <>
          <h3 style={{ fontSize: "1rem", marginTop: "1.25rem" }}>Warnings</h3>
          <ul style={{ margin: "0.35rem 0", paddingLeft: "1.2rem", fontSize: "0.9rem" }}>
            {d.warnings.map((w) => (
              <li key={w.code}>
                <code>{w.code}</code> — {w.message}
              </li>
            ))}
          </ul>
        </>
      ) : null}

      <h3 style={{ fontSize: "1rem", marginTop: "1.75rem" }}>Credential encryption</h3>
      {fingerprint.isPending ? <p>Loading key rotation summary…</p> : null}
      {fingerprint.isError ? <p>Credential key fingerprint unavailable.</p> : null}
      {fingerprint.data ? (
        <>
          <p>Current key version: {fingerprint.data.current_key_version}</p>
          <div className="lumogis-table-scroll">
            <table className="lumogis-dense-table">
              <thead>
                <tr>
                  <th>Tier</th>
                  <th>Key version</th>
                  <th>Count</th>
                </tr>
              </thead>
              <tbody>
                {tableRows.map((r, i) => (
                  <tr key={`${r.tier}-${r.keyVersion}-${i}`}>
                    <td>{r.tier}</td>
                    <td>{r.keyVersion}</td>
                    <td>{r.count}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      ) : null}

      <p style={{ marginTop: "1.5rem", fontSize: "0.9rem" }}>
        Need queue depth + per-user batch jobs? Tracked as follow-up{" "}
        <code>lumogis_web_admin_diagnostics_batch_jobs</code> (depends on <code>per_user_batch_jobs</code>
        ).
      </p>
    </section>
  );
}
