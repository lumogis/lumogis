// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Admin System status: GET /api/v1/admin/diagnostics/stack-status (read-only).

import { useQuery } from "@tanstack/react-query";

import { fetchAdminStackStatus, type StackServiceState } from "../../api/adminStackStatus";
import { ApiError } from "../../api/client";
import { useAuth } from "../../auth/AuthProvider";

function stateColour(state: StackServiceState): string {
  switch (state) {
    case "healthy":
      return "#2e7d32";
    case "degraded":
      return "#ed6c02";
    case "down":
      return "#c62828";
    case "not_configured":
      return "#757575";
    default:
      return "#616161";
  }
}

function storageBarColour(status: string): string {
  switch (status) {
    case "critical":
      return "#c62828";
    case "warn":
      return "#ed6c02";
    case "ok":
      return "#2e7d32";
    default:
      return "#9e9e9e";
  }
}

function formatBytes(n: number | null): string {
  if (n == null) return "—";
  const units = ["B", "KiB", "MiB", "GiB", "TiB"];
  let v = n;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i += 1;
  }
  return `${v.toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
}

export function AdminSystemStatusView(): JSX.Element {
  const { client } = useAuth();

  const status = useQuery({
    queryKey: ["admin", "stack-status"],
    queryFn: () => fetchAdminStackStatus(client),
    refetchInterval: (query) => (query.state.data ? 30_000 : false),
    refetchIntervalInBackground: false,
  });

  if (status.isPending) {
    return (
      <section aria-busy="true">
        <h2>System status</h2>
        <p>Loading…</p>
      </section>
    );
  }

  if (status.isError) {
    const err = status.error;
    const detail =
      err instanceof ApiError
        ? err.status === 401
          ? "Not signed in or session expired."
          : err.status === 403
            ? "Admin role required for system status."
            : err.detail
        : "System status unavailable.";
    return (
      <section>
        <h2>System status</h2>
        <p role="alert">{detail}</p>
      </section>
    );
  }

  const d = status.data;
  const showStackControlBanner =
    !d.meta.stack_control_reachable || d.warnings.some((w) => w.code.startsWith("stack_control"));

  return (
    <section className="lumogis-admin-dense-section">
      <h2>System status</h2>
      <p style={{ maxWidth: "42rem", opacity: 0.9 }}>
        Live stack health for household operators — services, host storage, and Ollama models
        (read-only). Polls every 30s while this tab is visible. For legacy JSON counts, see{" "}
        <a href="/health" target="_blank" rel="noopener noreferrer">
          stack health (legacy tab)
        </a>{" "}
        — prefer this panel for day-to-day checks.
      </p>

      {showStackControlBanner ? (
        <p role="status" style={{ color: "#ed6c02", maxWidth: "42rem" }}>
          Stack-control sidecar data is partial or unavailable. Service rows may rely on store
          pings only.
        </p>
      ) : null}

      <div style={{ margin: "1rem 0" }}>
        <div style={{ fontSize: "0.85rem", opacity: 0.85 }}>
          Overall: <strong>{d.meta.overall_status}</strong>
          {d.meta.cache_age_sec != null ? ` (cache ${d.meta.cache_age_sec}s)` : null}
        </div>
        <div style={{ fontSize: "0.85rem", opacity: 0.85 }}>Generated: {d.meta.generated_at}</div>
      </div>

      <h3 style={{ fontSize: "1rem", marginTop: "1.25rem" }}>Services</h3>
      <div className="lumogis-table-scroll">
        <table className="lumogis-dense-table">
          <thead>
            <tr>
              <th>Service</th>
              <th>State</th>
              <th>Runtime</th>
              <th>Note</th>
            </tr>
          </thead>
          <tbody>
            {d.services.map((s) => (
              <tr key={s.id}>
                <td>{s.display_name}</td>
                <td style={{ color: stateColour(s.state), fontWeight: 600 }}>{s.state}</td>
                <td style={{ fontSize: "0.85rem" }}>{s.runtime_kind}</td>
                <td style={{ fontSize: "0.85rem" }} className="lumogis-long-text">
                  {s.message ?? "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <h3 style={{ fontSize: "1rem", marginTop: "1.25rem" }}>Storage</h3>
      <ul style={{ listStyle: "none", padding: 0, margin: "0.5rem 0" }}>
        {d.storage.map((row) => (
          <li key={row.mount_id} style={{ marginBottom: "0.75rem", maxWidth: "36rem" }}>
            <div style={{ fontSize: "0.9rem", marginBottom: "0.25rem" }}>{row.path_label}</div>
            {row.used_percent != null && row.mount_id !== "docker_breakdown" ? (
              <div
                style={{
                  height: "8px",
                  background: "#e0e0e0",
                  borderRadius: 4,
                  overflow: "hidden",
                }}
              >
                <div
                  style={{
                    width: `${Math.min(100, row.used_percent)}%`,
                    height: "100%",
                    background: storageBarColour(row.status),
                  }}
                />
              </div>
            ) : null}
            <div style={{ fontSize: "0.8rem", opacity: 0.85 }}>
              {row.used_bytes != null && row.total_bytes != null
                ? `${formatBytes(row.used_bytes)} / ${formatBytes(row.total_bytes)}`
                : "—"}
              {row.used_percent != null ? ` (${row.used_percent}% used, ${row.status})` : ` (${row.status})`}
            </div>
          </li>
        ))}
      </ul>

      <h3 style={{ fontSize: "1rem", marginTop: "1.25rem" }}>Ollama models</h3>
      {d.ollama.length === 0 ? (
        <p style={{ fontSize: "0.9rem" }}>No local models reported (Ollama may be down).</p>
      ) : (
        <div className="lumogis-table-scroll">
          <table className="lumogis-dense-table">
            <thead>
              <tr>
                <th>Name</th>
                <th>Size</th>
                <th>Loaded</th>
              </tr>
            </thead>
            <tbody>
              {d.ollama.map((m) => (
                <tr key={m.name}>
                  <td style={{ fontFamily: "monospace", fontSize: "0.85rem" }} className="lumogis-long-text">
                    {m.name}
                  </td>
                  <td>{formatBytes(m.size_bytes)}</td>
                  <td>{m.loaded == null ? "—" : m.loaded ? "yes" : "no"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

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
    </section>
  );
}
