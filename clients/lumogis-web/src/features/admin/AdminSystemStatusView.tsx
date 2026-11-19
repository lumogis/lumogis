// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Admin → Health and backups: stack-status + backups + updates + Ollama pull/delete
// via legacy /settings/*. Rebuilt on the shared components in LUM-993 plan B Pass 6:
// one plain summary line, Backups, then each service with a status badge and one
// sentence. Versions, runtime kinds, raw codes and shell commands live in a collapsed
// "Details" at the bottom (the secure-address summary text comes from
// secureAddressCopy.ts).

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronDown } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";

import {
  deleteOllamaModel,
  fetchActiveOllamaPullJob,
  fetchOllamaDiscovery,
  fetchOllamaPullJob,
  startOllamaPull,
} from "../../api/adminOllama";
import {
  fetchAdminStackStatus,
  type StackServiceState,
  type StackStorageStatus,
} from "../../api/adminStackStatus";
import { fetchAdminBackupStatus } from "../../api/adminBackupStatus";
import { fetchAdminUpdateStatus, type UpdateStatusResponse } from "../../api/adminUpdateStatus";
import {
  BGE_RERANKER_RAM_ESTIMATE,
  fetchAdminSearchSettings,
  isRerankerBackendActive,
} from "../../api/searchSettings";
import { ApiError } from "../../api/client";
import { useAuth } from "../../auth/AuthProvider";
import { GraphOfferLine } from "../../components/GraphOfferLine";
import { ErrorState } from "../../components/page/ErrorState";
import { LoadingPlaceholder, SkeletonText } from "../../components/page/LoadingState";
import { PageSection, Row } from "../../components/page/PageSection";
import { Alert, AlertDescription } from "../../components/ui/alert";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "../../components/ui/alert-dialog";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "../../components/ui/collapsible";
import { Input } from "../../components/ui/input";
import { Label } from "../../components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "../../components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "../../components/ui/table";
import { formatDateTime } from "../../util/formatTimestamp";
import { SUMMARY_TEXT as SECURE_ADDRESS_SUMMARY } from "./secureAddressCopy";

type BadgeVariant = "default" | "secondary" | "destructive" | "outline";

const SERVICE_BADGE: Record<StackServiceState, { text: string; variant: BadgeVariant; sentence: string }> = {
  healthy: { text: "Running", variant: "default", sentence: "Working normally." },
  degraded: { text: "Needs attention", variant: "destructive", sentence: "Running, but not as well as it should." },
  down: { text: "Not running", variant: "destructive", sentence: "Lumogis cannot reach this right now." },
  not_configured: { text: "Not set up", variant: "outline", sentence: "Not set up on this Lumogis." },
  unknown: { text: "Unknown", variant: "secondary", sentence: "Lumogis could not tell whether this is working." },
};

const STORAGE_BADGE: Record<StackStorageStatus, { text: string; variant: BadgeVariant }> = {
  ok: { text: "Plenty of space", variant: "default" },
  warn: { text: "Getting full", variant: "destructive" },
  critical: { text: "Almost full", variant: "destructive" },
  unknown: { text: "Unknown", variant: "secondary" },
};

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

const UPDATE_DISMISS_KEY = "lumogis.admin.updateDismissedVersion";

function readDismissedVersion(): string | null {
  try {
    return localStorage.getItem(UPDATE_DISMISS_KEY);
  } catch {
    return null;
  }
}

function writeDismissedVersion(version: string): void {
  try {
    localStorage.setItem(UPDATE_DISMISS_KEY, version);
  } catch {
    // localStorage blocked — dismiss is a no-op
  }
}

/** True when the "newer release" notice is showing (not dismissed); the shell command for it lives in Details. */
function updateNoticeShowing(data: UpdateStatusResponse | undefined, dismissedVersion: string | null): boolean {
  if (!data) return false;
  const { checked, update_available, latest_version } = data;
  return Boolean(
    checked && update_available && !(latest_version != null && dismissedVersion === latest_version),
  );
}

function UpdateStatusCardBody({
  data,
  dismissedVersion,
  onDismiss,
}: {
  data: UpdateStatusResponse;
  dismissedVersion: string | null;
  onDismiss: (latestVersion: string) => void;
}): JSX.Element | null {
  const { checked, update_available, current_version, latest_version, checked_at, release_url, error } =
    data;
  const isDismissed =
    update_available && latest_version != null && dismissedVersion === latest_version;

  if (checked && update_available && !isDismissed) {
    return (
      <Alert>
        <AlertDescription className="flex flex-col gap-2">
          <span>
            A newer Lumogis release is available: <strong>{current_version}</strong>
            {latest_version != null ? (
              <>
                {" "}
                → <strong>{latest_version}</strong>
              </>
            ) : null}
            . Ask whoever runs the server to update it (the steps are under Details).
          </span>
          <span className="flex flex-wrap items-center gap-2">
            {release_url ? (
              <a
                href={release_url}
                target="_blank"
                rel="noopener noreferrer"
                className="text-accent-text underline underline-offset-2"
              >
                Release notes
              </a>
            ) : null}
            {latest_version != null ? (
              <Button
                type="button"
                variant="outline"
                size="sm"
                aria-label={`Dismiss update notification for version ${latest_version}`}
                onClick={() => onDismiss(latest_version)}
              >
                Dismiss
              </Button>
            ) : null}
          </span>
        </AlertDescription>
      </Alert>
    );
  }

  if (checked && update_available && isDismissed) {
    return null;
  }

  if (checked && !update_available && !error) {
    return (
      <p className="m-0 text-sm text-muted-foreground">
        Running the latest release (<strong>{current_version}</strong>).
        {checked_at ? ` Last checked: ${formatDateTime(checked_at)}.` : null}
      </p>
    );
  }

  if (checked && !update_available && error) {
    return (
      <p className="m-0 text-sm text-muted-foreground">
        {error}
        {latest_version ? ` (${latest_version})` : null}
      </p>
    );
  }

  return <p className="m-0 text-sm text-muted-foreground">{error ?? "Update check unavailable."}</p>;
}

export function AdminSystemStatusView(): JSX.Element {
  const { client } = useAuth();
  const queryClient = useQueryClient();
  const [pullNameInput, setPullNameInput] = useState("");
  const [selectedCatalogName, setSelectedCatalogName] = useState("");
  const [ollamaActionError, setOllamaActionError] = useState<string | null>(null);
  const [ollamaPullWarning, setOllamaPullWarning] = useState<string | null>(null);
  const [activeJobId, setActiveJobId] = useState<string | null>(null);
  const [dismissedVersion, setDismissedVersion] = useState<string | null>(() =>
    readDismissedVersion(),
  );
  const terminalHandledRef = useRef<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);

  const status = useQuery({
    queryKey: ["admin", "stack-status"],
    queryFn: () => fetchAdminStackStatus(client),
    refetchInterval: (query) => (query.state.data ? 30_000 : false),
    refetchIntervalInBackground: false,
  });

  const searchSettings = useQuery({
    queryKey: ["admin", "search-settings"],
    queryFn: () => fetchAdminSearchSettings(client),
    enabled: status.isSuccess,
    staleTime: 30_000,
  });

  const backupStatus = useQuery({
    queryKey: ["admin", "backup-status"],
    queryFn: () => fetchAdminBackupStatus(client),
    enabled: status.isSuccess,
    refetchInterval: (query) => (query.state.data ? 30_000 : false),
    refetchIntervalInBackground: false,
  });

  const updateStatus = useQuery({
    queryKey: ["admin", "update-status"],
    queryFn: () => fetchAdminUpdateStatus(client),
    enabled: status.isSuccess,
    refetchInterval: false,
    staleTime: 60 * 60 * 1000,
    refetchOnWindowFocus: false,
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

  // The words of the delete confirmation. Same facts as the old browser prompt: what it frees,
  // and whether the model is the embedding model, the default chat model, or a registered one.
  const deleteMessage = (name: string): string => {
    const disc = discovery.data;
    let msg = `Remove "${name}" from Ollama? This frees disk space.`;
    if (!disc) return msg;
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
    return msg;
  };


  if (status.isPending) {
    return (
      <LoadingPlaceholder label="Loading…" className="max-w-2xl">
        <SkeletonText lines={6} />
      </LoadingPlaceholder>
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
    return <ErrorState message={detail} onRetry={() => void status.refetch()} />;
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

  const troubled = d.services.filter((s) => s.state === "degraded" || s.state === "down");
  const summary =
    d.meta.overall_status === "ok" && troubled.length === 0
      ? "Everything is running."
      : troubled.length > 0
        ? `${d.meta.overall_status === "down" ? "Lumogis is not fully running" : "Lumogis is running, but some parts need attention"}: ${troubled.map((s) => s.display_name).join(", ")}.`
        : d.meta.overall_status === "down"
          ? "Lumogis is not fully running."
          : "Lumogis is running, but some parts need attention.";
  const summaryOk = d.meta.overall_status === "ok" && troubled.length === 0;

  const backup = backupStatus.data;
  const updateShowing = updateNoticeShowing(updateStatus.data, dismissedVersion);
  const coverage = backup?.coverage ?? [];

  return (
    <section data-testid="system-status" className="flex flex-col gap-2">
      <p
        data-testid="system-status-summary"
        className="m-0 flex max-w-2xl items-center gap-2 text-sm font-medium"
      >
        <Badge variant={summaryOk ? "default" : "destructive"}>{summaryOk ? "All good" : "Needs attention"}</Badge>
        <span>{summary}</span>
      </p>

      {showStackControlBanner ? (
        <p role="status" className="m-0 max-w-2xl text-sm text-muted-foreground">
          Lumogis cannot see every part of the system right now, so the list below may be incomplete.
        </p>
      ) : null}

      {backup ? (
        <PageSection
          title="Backups"
          description="A backup is a copy of your Lumogis data you can restore after a disk failure."
        >
          {backup.stale || backup.warnings.length > 0 ? (
            <Alert variant="destructive" className="mb-3">
              <AlertDescription className="flex flex-col gap-1">
                {backup.warnings.length > 0 ? (
                  // Every warning, not the first: a stale-backup warning must not hide
                  // "files are missing from disk" (LUM-892).
                  backup.warnings.map((w) => <p key={w.code} className="m-0">{w.message}</p>)
                ) : (
                  <p className="m-0">
                    {`The last backup is more than ${backup.stale_threshold_hours} hours old. Ask whoever runs the server to take a new one (see Details).`}
                  </p>
                )}
              </AlertDescription>
            </Alert>
          ) : null}
          <dl className="m-0">
            <Row label="Status">
              <Badge variant={backup.enabled ? "default" : "outline"}>{backup.enabled ? "On" : "Off"}</Badge>
            </Row>
            <Row label="Last backup">
              {backup.last_success_at ? formatDateTime(backup.last_success_at) : "No backup yet"}
            </Row>
            <Row label="Size">{formatBytes(backup.total_bytes)}</Row>
            <Row label="Checked">
              {backup.last_verify_status === "ok"
                ? "The last backup checked out fine"
                : backup.last_verify_status === "failed"
                  ? "The last backup failed its check"
                  : "Not checked yet"}
            </Row>
          </dl>
          <p className="mt-3 mb-0 text-sm text-muted-foreground">
            To restore, ask whoever runs the server. A restore is done on that computer, not from this page;
            the steps are under Details.
          </p>
          {coverage.length > 0 ? (
            <div className="mt-3 flex flex-col gap-3 text-sm">
              {(
                [
                  ["Covered", true],
                  ["Not covered", false],
                ] as const
              ).map(([heading, covered]) => (
                <div key={heading}>
                  <h3 className="m-0 mb-1 text-sm font-semibold">{heading}</h3>
                  <ul className="m-0 list-disc pl-5">
                    {coverage
                      .filter((c) => c.covered === covered)
                      .map((c) => (
                        <li key={c.id}>{c.note ? `${c.label} — ${c.note}` : c.label}</li>
                      ))}
                  </ul>
                </div>
              ))}
            </div>
          ) : null}
        </PageSection>
      ) : backupStatus.isError ? (
        <p className="m-0 max-w-2xl text-sm text-muted-foreground">Backup status unavailable.</p>
      ) : null}

      <PageSection title="Services" description="The parts Lumogis is made of.">
        <dl className="m-0">
          {d.services.map((s) => {
            const b = SERVICE_BADGE[s.state] ?? SERVICE_BADGE.unknown;
            return (
              <Row key={s.id} label={s.display_name}>
                <span className="flex flex-wrap items-center justify-end gap-2">
                  <Badge variant={b.variant}>{b.text}</Badge>
                  <span className="font-normal text-muted-foreground">{b.sentence}</span>
                </span>
              </Row>
            );
          })}
        </dl>
      </PageSection>

      <PageSection title="Disk space">
        <dl className="m-0">
          {d.storage.map((row) => {
            const b = STORAGE_BADGE[row.status] ?? STORAGE_BADGE.unknown;
            return (
              <Row key={row.mount_id} label={row.path_label}>
                <span className="flex flex-wrap items-center justify-end gap-2">
                  {row.used_bytes != null && row.total_bytes != null
                    ? `${formatBytes(row.used_bytes)} of ${formatBytes(row.total_bytes)}`
                    : "—"}
                  {row.used_percent != null ? ` (${row.used_percent}% used)` : null}
                  <Badge variant={b.variant}>{b.text}</Badge>
                </span>
              </Row>
            );
          })}
        </dl>
      </PageSection>

      {updateStatus.data ? (
        <PageSection title="Software updates">
          <UpdateStatusCardBody
            data={updateStatus.data}
            dismissedVersion={dismissedVersion}
            onDismiss={(latestVersion) => {
              writeDismissedVersion(latestVersion);
              setDismissedVersion(latestVersion);
            }}
          />
        </PageSection>
      ) : updateStatus.isError ? (
        <p className="m-0 max-w-2xl text-sm text-muted-foreground">Software update status unavailable.</p>
      ) : null}

      {d.secure_address ? (
        <p data-testid="system-status-secure-address" className="m-0 max-w-2xl text-sm">
          Secure address: {SECURE_ADDRESS_SUMMARY[d.secure_address.state]}
          {d.secure_address.url ? (
            <>
              {" "}
              (
              <a href={d.secure_address.url} className="text-accent-text underline underline-offset-2">
                {d.secure_address.url}
              </a>
              )
            </>
          ) : null}{" "}
          ·{" "}
          <Link to="/settings/home-address" className="text-accent-text underline underline-offset-2">
            Open Home address
          </Link>
        </p>
      ) : null}

      <GraphOfferLine />

      {/* LUM-792 — grouped so a test can say "no error from the Ollama mutation"
          without asserting on the whole System status section, which also carries the
          DR-backup staleness advisory (permanently present on a fresh stack, where no
          backup has run). Also a real a11y gain: a named region. */}
      <section aria-label="Ollama models">
        <PageSection
          title="Ollama models"
          description="The language models stored on this computer. Pulling a model downloads it; deleting one frees disk space."
        >
          {discoveryError ? (
            <Alert variant="destructive" className="mb-3">
              <AlertDescription>{discoveryError}</AlertDescription>
            </Alert>
          ) : null}

          {ollamaActionError ? (
            <Alert variant="destructive" className="mb-3">
              <AlertDescription>{ollamaActionError}</AlertDescription>
            </Alert>
          ) : null}

          {ollamaPullWarning ? (
            <p role="status" className="mb-3 text-sm text-destructive">
              {ollamaPullWarning}
            </p>
          ) : null}

          {disc ? (
            <div className="mb-4 flex flex-col gap-3" aria-busy={pullInFlight ? "true" : undefined}>
              <div className="flex flex-wrap items-end gap-3">
                <div className="flex flex-col gap-1.5">
                  <Label htmlFor="ollama-catalog">Catalog</Label>
                  <Select
                    value={selectedCatalogName}
                    onValueChange={(v) => setSelectedCatalogName(String(v ?? ""))}
                    disabled={pullBusy}
                  >
                    <SelectTrigger id="ollama-catalog" aria-label="Ollama catalog model" className="min-w-44">
                      <SelectValue placeholder="— select —" />
                    </SelectTrigger>
                    <SelectContent>
                      {disc.catalog.map((entry) => (
                        <SelectItem key={entry.name} value={entry.name}>
                          {entry.display_name}
                          {entry.installed ? " (installed)" : ""}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
                <div className="flex min-w-0 flex-1 basis-48 flex-col gap-1.5">
                  <Label htmlFor="ollama-model-name">Model name</Label>
                  <Input
                    id="ollama-model-name"
                    type="text"
                    value={pullNameInput}
                    onChange={(e) => setPullNameInput(e.target.value)}
                    placeholder="catalog or typed name"
                    disabled={pullBusy}
                    aria-label="Ollama model name to pull"
                  />
                </div>
                <Button
                  type="button"
                  disabled={!pullTarget || pullBusy}
                  onClick={() => pullM.mutate(pullTarget)}
                >
                  Pull
                </Button>
              </div>
              {pullTarget && isEmbeddingPullTarget(pullTarget, disc.embedding_model) ? (
                <p className="m-0 text-sm text-destructive">
                  Pulling the embedding model may re-initialize Qdrant collections.
                </p>
              ) : null}
              {pullInFlight && pullJob ? (
                <div className="max-w-sm">
                  <p className="mt-0 mb-1 text-sm">
                    Pulling {pullJob.model_name}
                    {pullJob.status_message ? ` — ${pullJob.status_message}` : ""}
                  </p>
                  {pullJob.progress_pct != null ? (
                    <progress
                      value={pullJob.progress_pct}
                      max={100}
                      aria-label="Ollama pull progress"
                      className="h-2 w-full"
                    />
                  ) : (
                    <p className="m-0 text-sm text-muted-foreground">Preparing download…</p>
                  )}
                </div>
              ) : null}
            </div>
          ) : null}

          {d.ollama.length === 0 ? (
            <p className="m-0 text-sm text-muted-foreground">
              No local models reported (Ollama may be down). Pull one above once it is back.
            </p>
          ) : (
            // On a phone the rows stack (as the Activity table does): five columns
            // squeezed model names to one letter per line and cut off Delete.
            <Table>
              <TableHeader className="max-sm:hidden">
                <TableRow>
                  <TableHead>Name</TableHead>
                  <TableHead>Used for</TableHead>
                  <TableHead>Size</TableHead>
                  <TableHead>In memory</TableHead>
                  <TableHead>
                    <span className="sr-only">Actions</span>
                  </TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {d.ollama.map((m) => {
                  const registryAlias =
                    disc != null ? resolveRegistryAlias(m.name, disc.alias_map) : null;
                  const embedding =
                    disc != null && isEmbeddingModel(m.name, disc.embedding_model);

                  return (
                    <TableRow key={m.name} className="max-sm:flex max-sm:flex-wrap max-sm:items-center max-sm:gap-x-3 max-sm:py-2">
                      <TableCell className="font-mono text-xs break-all whitespace-normal max-sm:w-full max-sm:pb-0 max-sm:text-sm">
                        {m.name}
                      </TableCell>
                      <TableCell>
                        {embedding ? (
                          <Badge variant="secondary">embedding</Badge>
                        ) : registryAlias ? (
                          <Badge variant="secondary">{registryAlias}</Badge>
                        ) : (
                          "—"
                        )}
                      </TableCell>
                      <TableCell className="text-muted-foreground sm:text-foreground">{formatBytes(m.size_bytes)}</TableCell>
                      <TableCell className="text-muted-foreground sm:text-foreground">
                        <span className="sm:hidden">In memory: </span>
                        {m.loaded == null ? "—" : m.loaded ? "yes" : "no"}
                      </TableCell>
                      <TableCell className="text-right max-sm:ml-auto">
                        <Button
                          type="button"
                          variant="destructive"
                          size="sm"
                          aria-label={`Delete ${m.name}`}
                          disabled={pullBusy || !disc}
                          onClick={() => setConfirmDelete(m.name)}
                        >
                          Delete
                        </Button>
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          )}
        </PageSection>
      </section>

      <AlertDialog open={confirmDelete !== null} onOpenChange={(open) => !open && setConfirmDelete(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete this model?</AlertDialogTitle>
            <AlertDialogDescription>
              {confirmDelete !== null ? deleteMessage(confirmDelete) : ""}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              variant="destructiveSolid"
              onClick={() => {
                const name = confirmDelete;
                setConfirmDelete(null);
                if (name !== null) deleteM.mutate(name);
              }}
            >
              Delete model
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <Collapsible className="max-w-2xl">
        <CollapsibleTrigger
          render={<Button type="button" variant="ghost" size="sm" className="-ml-2 group/trigger" />}
        >
          <ChevronDown aria-hidden="true" className="transition-transform group-aria-expanded/trigger:rotate-180" />
          Details
        </CollapsibleTrigger>
        {/* keepMounted: the developer rows stay in the page (as they were) while closed. */}
        <CollapsibleContent keepMounted className="flex flex-col gap-4 pt-2 text-sm">
          <p className="m-0 text-muted-foreground">For whoever runs the server.</p>
          <dl className="m-0">
            <Row label="Overall status">
              {d.meta.overall_status}
              {d.meta.cache_age_sec != null ? ` (cache ${d.meta.cache_age_sec}s)` : null}
            </Row>
            <Row label="Generated">{formatDateTime(d.meta.generated_at)}</Row>
            {searchSettings.data &&
            isRerankerBackendActive(searchSettings.data.reranker_backend_live) &&
            !searchSettings.data.reranker_pending_restart ? (
              <Row label="Search ranking">
                <Badge variant="secondary" data-testid="reranker-active-chip">
                  Reranker active ({BGE_RERANKER_RAM_ESTIMATE})
                </Badge>
              </Row>
            ) : null}
          </dl>

          <div>
            <h3 className="m-0 mb-1 text-sm font-semibold">Services</h3>
            <ul className="m-0 list-disc pl-5">
              {d.services.map((s) => (
                <li key={s.id}>{`${s.display_name}: ${s.state}, ${s.runtime_kind}${s.message ? ` — ${s.message}` : ""}`}</li>
              ))}
            </ul>
          </div>

          {backup ? (
            <div>
              <h3 className="m-0 mb-1 text-sm font-semibold">Backup</h3>
              <ul className="m-0 list-disc pl-5">
                <li>{`Last snapshot: ${backup.last_snapshot_id ?? "none"}`}</li>
                <li>
                  {`Age: ${backup.age_hours != null ? `${backup.age_hours.toFixed(1)}h` : "—"}`}
                </li>
                <li>
                  Stores:{" "}
                  {backup.stores
                    .map((st) => {
                      if (st.skipped) return `${st.id} (skipped)`;
                      if (!st.present) return `${st.id} (missing)`;
                      return st.id === "files" && st.file_count != null
                        ? `files (${st.file_count} files)`
                        : st.id;
                    })
                    .join(", ")}
                </li>
                <li>{`Verify: ${backup.last_verify_status ?? "unknown"}`}</li>
                <li>
                  Take a backup now with <code>make backup</code> on the host. Restoring is covered in the
                  reference manual (Disaster recovery).
                </li>
              </ul>
            </div>
          ) : null}

          {updateShowing ? (
            <p className="m-0">
              Update on the host with <code>make update</code> (see the reference manual — Update mechanism).
            </p>
          ) : null}

          {d.warnings.length > 0 ? (
            <div>
              <h3 className="m-0 mb-1 text-sm font-semibold">Warnings</h3>
              <ul className="m-0 list-disc pl-5">
                {d.warnings.map((w) => (
                  <li key={w.code}>
                    <code>{w.code}</code> — {w.message}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
        </CollapsibleContent>
      </Collapsible>
    </section>
  );
}
