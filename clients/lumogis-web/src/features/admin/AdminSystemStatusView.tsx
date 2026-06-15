// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Admin System status: stack-status + Ollama pull/delete via legacy /settings/*.

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

import {
  deleteOllamaModel,
  fetchActiveOllamaPullJob,
  fetchOllamaDiscovery,
  fetchOllamaPullJob,
  startOllamaPull,
} from "../../api/adminOllama";
import { fetchAdminStackStatus, type StackServiceState } from "../../api/adminStackStatus";
import { fetchAdminBackupStatus } from "../../api/adminBackupStatus";
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

function errMsg(e: unknown): string {
  if (e instanceof ApiError) return e.detail;
  if (e instanceof Error) return e.message;
  return "Request failed";
}

function modelBase(name: string | null | undefined): string {
  if (!name) return "";
  return name.split(":")[0];
}

function resolveRegistryAlias(name: string, aliasMap: Record<string, string>): string | null {
  return aliasMap[name] ?? aliasMap[modelBase(name)] ?? null;
}

function isEmbeddingModel(name: string, embeddingModel: string | null | undefined): boolean {
  if (!embeddingModel) return false;
  return modelBase(name) === modelBase(embeddingModel);
}

function isDefaultChatModel(
  name: string,
  aliasMap: Record<string, string>,
  defaultModel: string | null,
): boolean {
  return defaultModel != null && resolveRegistryAlias(name, aliasMap) === defaultModel;
}

function isEmbeddingPullTarget(name: string, embeddingModel: string | null | undefined): boolean {
  return isEmbeddingModel(name, embeddingModel);
}

export function AdminSystemStatusView(): JSX.Element {
  const { client } = useAuth();
  const queryClient = useQueryClient();
  const [pullNameInput, setPullNameInput] = useState("");
  const [selectedCatalogName, setSelectedCatalogName] = useState("");
  const [ollamaActionError, setOllamaActionError] = useState<string | null>(null);
  const [ollamaPullWarning, setOllamaPullWarning] = useState<string | null>(null);
  const [activeJobId, setActiveJobId] = useState<string | null>(null);
  const terminalHandledRef = useRef<string | null>(null);

  const status = useQuery({
    queryKey: ["admin", "stack-status"],
    queryFn: () => fetchAdminStackStatus(client),
    refetchInterval: (query) => (query.state.data ? 30_000 : false),
    refetchIntervalInBackground: false,
  });

  const backupStatus = useQuery({
    queryKey: ["admin", "backup-status"],
    queryFn: () => fetchAdminBackupStatus(client),
    enabled: status.isSuccess,
    refetchInterval: (query) => (query.state.data ? 30_000 : false),
    refetchIntervalInBackground: false,
  });

  const discovery = useQuery({
    queryKey: ["admin", "ollama-discovery"],
    queryFn: () => fetchOllamaDiscovery(client),
    enabled: status.isSuccess,
    staleTime: 60_000,
  });

  const invalidateOllama = (): void => {
    void queryClient.invalidateQueries({ queryKey: ["admin", "stack-status"] });
    void queryClient.invalidateQueries({ queryKey: ["admin", "ollama-discovery"] });
  };

  const activePullOnMount = useQuery({
    queryKey: ["admin", "ollama-pull-active"],
    queryFn: () => fetchActiveOllamaPullJob(client),
    enabled: status.isSuccess,
    staleTime: 0,
  });

  useEffect(() => {
    const job = activePullOnMount.data?.job;
    if (job && (job.status === "pending" || job.status === "running")) {
      setActiveJobId(job.job_id);
    }
  }, [activePullOnMount.data]);

  const pullJobQuery = useQuery({
    queryKey: ["admin", "ollama-pull-job", activeJobId],
    queryFn: () => fetchOllamaPullJob(client, activeJobId!),
    enabled: activeJobId != null,
    refetchInterval: (query) => {
      const s = query.state.data?.status;
      return s === "pending" || s === "running" ? 1500 : false;
    },
    refetchIntervalInBackground: true,
  });

  useEffect(() => {
    const job = pullJobQuery.data;
    if (!job) return;
    if (job.status !== "succeeded" && job.status !== "failed") return;
    if (terminalHandledRef.current === job.job_id) return;
    terminalHandledRef.current = job.job_id;
    if (job.status === "succeeded") {
      setOllamaActionError(null);
      const warning = job.qdrant_init_warning?.trim();
      setOllamaPullWarning(warning ? warning : null);
      void queryClient.invalidateQueries({ queryKey: ["admin", "stack-status"] });
      void queryClient.invalidateQueries({ queryKey: ["admin", "ollama-discovery"] });
      setActiveJobId(null);
      return;
    }
    setOllamaPullWarning(null);
    setOllamaActionError(job.error_message?.trim() || "Ollama pull failed");
    setActiveJobId(null);
  }, [pullJobQuery.data, queryClient]);

  const pullM = useMutation({
    mutationFn: (name: string) => startOllamaPull(client, name),
    onMutate: () => {
      setOllamaPullWarning(null);
      terminalHandledRef.current = null;
    },
    onSuccess: (data) => {
      setOllamaActionError(null);
      setActiveJobId(data.job_id);
    },
    onError: (e) => {
      setOllamaPullWarning(null);
      setOllamaActionError(errMsg(e));
    },
  });

  const deleteM = useMutation({
    mutationFn: (name: string) => deleteOllamaModel(client, name),
    onSuccess: () => {
      setOllamaActionError(null);
      setOllamaPullWarning(null);
      invalidateOllama();
    },
    onError: (e) => setOllamaActionError(errMsg(e)),
  });

  const pullTarget = pullNameInput.trim() || selectedCatalogName;
  const pullJob = pullJobQuery.data;
  const pullInFlight =
    pullJob?.status === "pending" ||
    pullJob?.status === "running" ||
    (activeJobId != null && pullJobQuery.isPending);
  const pullBusy = pullM.isPending || deleteM.isPending || pullInFlight;

  const handleDelete = (name: string): void => {
    const disc = discovery.data;
    if (!disc) return;

    let msg = `Remove "${name}" from Ollama? This frees disk space.`;
    if (isEmbeddingModel(name, disc.embedding_model)) {
      msg += " This is the household embedding model.";
    } else if (isDefaultChatModel(name, disc.alias_map, disc.default_model)) {
      msg += ` This model is the active default chat model (${disc.default_model}).`;
    } else {
      const alias = resolveRegistryAlias(name, disc.alias_map);
      if (alias) {
        msg += ` This model is registered in the model config as ${alias}.`;
      }
    }
    if (!window.confirm(msg)) return;
    deleteM.mutate(name);
  };

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

  const disc = discovery.data;
  const discoveryError =
    discovery.isError && !discovery.isPending
      ? discovery.error instanceof ApiError
        ? discovery.error.detail
        : "Ollama discovery unavailable."
      : null;

  return (
    <section className="lumogis-admin-dense-section">
      <h2>System status</h2>
      <p style={{ maxWidth: "42rem", opacity: 0.9 }}>
        Live stack health for household operators — services, host storage, and Ollama models.
        Pull and delete models below; pulls run in the background with a progress bar. Polls every
        30s while this
        tab is visible. For legacy JSON counts, see{" "}
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

      {backupStatus.data ? (
        <div
          style={{
            margin: "1rem 0",
            padding: "0.75rem 1rem",
            border: "1px solid #e0e0e0",
            borderRadius: 6,
            maxWidth: "42rem",
          }}
        >
          <h3 style={{ fontSize: "1rem", margin: "0 0 0.5rem" }}>Disaster recovery backup</h3>
          {backupStatus.data.stale || backupStatus.data.warnings.length > 0 ? (
            <p role="alert" style={{ color: "#ed6c02", fontSize: "0.9rem" }}>
              {backupStatus.data.warnings[0]?.message ??
                `Last backup is older than ${backupStatus.data.stale_threshold_hours}h — run make backup on the host.`}
            </p>
          ) : null}
          <div style={{ fontSize: "0.85rem", lineHeight: 1.5 }}>
            <div>
              Enabled: <strong>{backupStatus.data.enabled ? "yes" : "no"}</strong>
            </div>
            <div>
              Last snapshot:{" "}
              <strong>{backupStatus.data.last_snapshot_id ?? "none"}</strong>
              {backupStatus.data.last_success_at ? ` (${backupStatus.data.last_success_at})` : null}
            </div>
            <div>
              Age:{" "}
              {backupStatus.data.age_hours != null
                ? `${backupStatus.data.age_hours.toFixed(1)}h`
                : "—"}
              {" · "}
              Size: {formatBytes(backupStatus.data.total_bytes)}
            </div>
            <div>
              Stores:{" "}
              {backupStatus.data.stores
                .map((s) => `${s.id}${s.skipped ? " (skipped)" : s.present ? "" : " (missing)"}`)
                .join(", ")}
            </div>
            <div>
              Verify: <strong>{backupStatus.data.last_verify_status ?? "unknown"}</strong>
            </div>
          </div>
        </div>
      ) : backupStatus.isError ? (
        <p style={{ fontSize: "0.85rem", opacity: 0.85 }}>DR backup status unavailable.</p>
      ) : null}

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

      {discoveryError ? (
        <p role="alert" style={{ color: "#c62828", fontSize: "0.9rem" }}>
          {discoveryError}
        </p>
      ) : null}

      {ollamaActionError ? (
        <p role="alert" style={{ color: "#c62828", fontSize: "0.9rem" }}>
          {ollamaActionError}
        </p>
      ) : null}

      {ollamaPullWarning ? (
        <p role="status" style={{ color: "#e65100", fontSize: "0.9rem" }}>
          {ollamaPullWarning}
        </p>
      ) : null}

      {disc ? (
        <div
          style={{ marginBottom: "1rem", maxWidth: "42rem" }}
          aria-busy={pullInFlight ? "true" : undefined}
        >
          <div style={{ display: "flex", flexWrap: "wrap", gap: "0.5rem", alignItems: "center" }}>
            <label style={{ fontSize: "0.85rem" }}>
              Catalog{" "}
              <select
                value={selectedCatalogName}
                onChange={(e) => setSelectedCatalogName(e.target.value)}
                disabled={pullBusy}
                aria-label="Ollama catalog model"
              >
                <option value="">— select —</option>
                {disc.catalog.map((entry) => (
                  <option key={entry.name} value={entry.name}>
                    {entry.display_name}
                    {entry.installed ? " (installed)" : ""}
                  </option>
                ))}
              </select>
            </label>
            <label style={{ fontSize: "0.85rem" }}>
              Model name{" "}
              <input
                type="text"
                value={pullNameInput}
                onChange={(e) => setPullNameInput(e.target.value)}
                placeholder="catalog or typed name"
                disabled={pullBusy}
                aria-label="Ollama model name to pull"
              />
            </label>
            <button
              type="button"
              disabled={!pullTarget || pullBusy}
              onClick={() => pullM.mutate(pullTarget)}
            >
              Pull
            </button>
          </div>
          {pullTarget && isEmbeddingPullTarget(pullTarget, disc.embedding_model) ? (
            <p style={{ fontSize: "0.85rem", color: "#ed6c02", margin: "0.5rem 0 0" }}>
              Pulling the embedding model may re-initialize Qdrant collections.
            </p>
          ) : null}
          {pullInFlight && pullJob ? (
            <div style={{ margin: "0.5rem 0 0", maxWidth: "24rem" }}>
              <p style={{ fontSize: "0.85rem", margin: "0 0 0.35rem" }}>
                Pulling {pullJob.model_name}
                {pullJob.status_message ? ` — ${pullJob.status_message}` : ""}
              </p>
              {pullJob.progress_pct != null ? (
                <progress
                  value={pullJob.progress_pct}
                  max={100}
                  aria-label="Ollama pull progress"
                  style={{ width: "100%" }}
                />
              ) : (
                <p style={{ fontSize: "0.8rem", opacity: 0.85 }}>Preparing download…</p>
              )}
            </div>
          ) : null}
        </div>
      ) : null}

      {d.ollama.length === 0 ? (
        <p style={{ fontSize: "0.9rem" }}>No local models reported (Ollama may be down).</p>
      ) : (
        <div className="lumogis-table-scroll">
          <table className="lumogis-dense-table">
            <thead>
              <tr>
                <th>Name</th>
                <th>Role</th>
                <th>Size</th>
                <th>Loaded</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {d.ollama.map((m) => {
                const registryAlias =
                  disc != null ? resolveRegistryAlias(m.name, disc.alias_map) : null;
                const embedding =
                  disc != null && isEmbeddingModel(m.name, disc.embedding_model);
                const isDefault =
                  disc != null &&
                  isDefaultChatModel(m.name, disc.alias_map, disc.default_model);

                return (
                  <tr key={m.name}>
                    <td style={{ fontFamily: "monospace", fontSize: "0.85rem" }} className="lumogis-long-text">
                      {m.name}
                    </td>
                    <td style={{ fontSize: "0.85rem" }}>
                      {embedding ? (
                        <span
                          style={{
                            background: "#e3f2fd",
                            padding: "0.1rem 0.35rem",
                            borderRadius: 3,
                            fontWeight: isDefault ? 700 : 400,
                          }}
                        >
                          embedding
                        </span>
                      ) : registryAlias ? (
                        <span
                          style={{
                            background: "#f3e5f5",
                            padding: "0.1rem 0.35rem",
                            borderRadius: 3,
                            fontWeight: isDefault ? 700 : 400,
                          }}
                        >
                          {registryAlias}
                        </span>
                      ) : (
                        "—"
                      )}
                    </td>
                    <td>{formatBytes(m.size_bytes)}</td>
                    <td>{m.loaded == null ? "—" : m.loaded ? "yes" : "no"}</td>
                    <td>
                      <button
                        type="button"
                        disabled={pullBusy}
                        onClick={() => handleDelete(m.name)}
                      >
                        Delete
                      </button>
                    </td>
                  </tr>
                );
              })}
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
