// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
// Email IMAP source + signal list façade (LUM-170 / LUM-627).
import type { ApiClient } from "./client";

export type ImportScope = "just_new" | "last_7d" | "last_30d" | "custom";

export interface EmailSourceCreateRequest {
  host: string;
  port?: number;
  mailbox_label: string;
  name?: string;
  full_ingest?: boolean;
  poll_interval?: number;
  // LUM-644 import scope. Custom takes a start (and optional end) date; every
  // other scope reduces to "a start date -> now".
  import_scope?: ImportScope;
  import_since?: string; // YYYY-MM-DD
  import_before?: string; // YYYY-MM-DD (custom, optional)
}

// LUM-644 static provider registry (GET /api/v1/me/email-providers).
export interface ProviderInfo {
  id: string;
  display_name: string;
  imap_host: string;
  imap_port: number;
  smtp_host: string;
  smtp_port: number;
  app_password_url: string;
  help_steps: string[];
  auth_status: "app_password" | "oauth_only";
  enabled: boolean;
  disabled_reason: string | null;
  notes: string;
  // LUM-645: does the provider DISPLAY Lumogis labels? Gmail=true; iCloud/Fastmail
  // store IMAP keywords their common clients don't surface → the UI steers those
  // users to folder-sort and shows a caveat on "label". Optional-safe vs older backend.
  label_visible?: boolean;
  // LUM-687: OAuth (device-flow) connect available for this provider on THIS
  // install — the backend flips it true only when the operator's BYO client_id is
  // configured. Optional-safe against an older backend (absent → falsy → hidden).
  supports_oauth?: boolean;
  oauth_provider?: string | null;
}

// LUM-687 — OAuth Device Authorization Grant (RFC 8628) connect. The device_code
// stays server-side; the browser only ever sees the user_code + verification URL,
// then polls. POST /me/email-sources/oauth/{provider}/device/{start,poll}.
export interface OAuthDeviceStartRequest {
  email: string;
  mailbox_label: string;
  // LUM-691 — import-scope parity with the app-password EmailSourceCreateRequest
  // path. Same rules: custom needs import_since; non-custom forbids both dates.
  import_scope?: ImportScope;
  import_since?: string; // YYYY-MM-DD
  import_before?: string; // YYYY-MM-DD
}

export interface OAuthDeviceStartResponse {
  flow_id: string;
  user_code: string;
  verification_uri: string;
  verification_uri_complete?: string | null;
  interval: number;
  expires_in: number;
}

export type OAuthDevicePollStatus =
  | "pending"
  | "connected"
  | "expired"
  | "denied"
  | "error";

export interface OAuthDevicePollResponse {
  status: OAuthDevicePollStatus;
  source_id?: string | null;
  // false on a soft-save (egress-proxied deployment) — the mailbox is connected
  // but live IMAP verification defers to the first sync.
  verified?: boolean;
  error?: string | null;
}

export function startOAuthDevice(
  client: ApiClient,
  provider: string,
  body: OAuthDeviceStartRequest,
): Promise<OAuthDeviceStartResponse> {
  return client.postJson(
    `/me/email-sources/oauth/${encodeURIComponent(provider)}/device/start`,
    body,
  );
}

export function pollOAuthDevice(
  client: ApiClient,
  provider: string,
  flowId: string,
): Promise<OAuthDevicePollResponse> {
  return client.postJson(
    `/me/email-sources/oauth/${encodeURIComponent(provider)}/device/poll`,
    { flow_id: flowId },
  );
}

// LUM-644 pre-save live-test contract (POST /me/email-sources/test).
export interface ProbeFolder {
  name: string;
  role: string | null;
  hidden: boolean;
  selected: boolean;
}

export interface EmailProbeResponse {
  ok: boolean;
  message_count: number | null;
  folders: ProbeFolder[];
  capabilities: string[];
  error: string | null;
  proxy_unverified: boolean;
}

export interface EmailSourceCreateResponse {
  status: string;
  source_id: string;
  source_type: string;
  url: string;
  extraction_method: string;
}

/** How a mailbox signs in. See `SourceRow.auth_method`. */
export type MailboxAuthMethod = "oauth" | "app_password";

export interface SourceRow {
  id: string;
  name: string;
  source_type: string;
  url: string;
  category: string;
  active: boolean;
  poll_interval: number;
  // LUM-628/639: the mailbox's ingest mode and whether full-ingest is actually in
  // effect (mode is imap_full_ingest AND the operator flag EMAIL_FULL_INGEST_ENABLED
  // is on). Optional-safe against an older backend that doesn't return them yet.
  extraction_method?: string;
  full_ingest_active?: boolean;
  last_polled_at: string | null;
  last_signal_at: string | null;
  polling_active: boolean;
  // LUM-690/633: a mailbox that can't currently authenticate (e.g. a revoked
  // OAuth grant, or a soft-saved OAuth connect on a proxied deployment whose
  // first real sync then fails) — `null` = healthy. Optional-safe against an
  // older backend that doesn't return them yet.
  last_poll_error_code?: string | null;
  last_poll_error_at?: string | null;
  // LUM-742: the attachment-filing opt-in and its watermark. Written since
  // LUM-702, readable only from LUM-742 onward — `AttachmentIngestToggle`
  // takes `enabled` as a required prop, so before this there was no way to
  // render it truthfully. Optional-safe against an older backend exactly as
  // `extraction_method` is: absent reads as off, which is the safe direction.
  attachment_ingest_enabled?: boolean;
  // Absent = never enabled, so a first enable starts from now and nothing is
  // pending until new mail arrives. Present = a previous opt-in, so re-enabling
  // resumes from it and surfaces everything accumulated while it was off.
  attachment_ingest_since?: string | null;
  // LUM-771: would enabling send on this mailbox need a submission host/port?
  // Derived server-side on every read from `sources.url` by the same
  // `provider_for_host` the opt-in route matches with — one matcher, never a
  // stored column. `true` means "a host is required, OR the server could not
  // tell": it degrades to `true` for a url it cannot parse, so it is a request
  // to ask, never a diagnosis. Do not word it as "this provider is unknown".
  //
  // Optional-safe against an older backend, and **absent reads as `false`** —
  // the safe direction here, because a spurious `true` shows two fields to
  // someone who does not need them, while a spurious `false` still ends in
  // `422 smtp_host_required`, which reveals them (LUM-769 Decision 2d).
  smtp_host_required?: boolean;
  // LUM-777: how this mailbox signs in. Derived server-side from the plaintext
  // key NAMES of its IMAP credential row — never stored, never decrypted.
  //
  // **Absent and `null` both mean UNKNOWN.** Branch only on a known value and
  // never coerce `null` to either one: a spurious `app_password` on an OAuth
  // mailbox brings back the password-field trap, while a spurious unknown only
  // shows the hedged copy. A mailbox saved before migration 079, a non-IMAP row,
  // an inconclusive credential and a failed credential read are all `null`.
  //
  // Display-only, never authorisation: the server keeps deciding with
  // `smtp_oauth_denied` / `smtp_password_not_applicable`. Optional-safe against
  // an older backend.
  auth_method?: MailboxAuthMethod | null;
}

export interface UpdateSourceResponse {
  status: string;
  source_id: string;
  source_type: string;
  extraction_method: string;
  // LUM-837: `null` when the request never supplied `full_ingest`. `false`
  // already means "requested and inert because EMAIL_FULL_INGEST_ENABLED is
  // off", so a request that never mentioned mode must not assert anything
  // about it — which is why this is nullable rather than defaulted.
  full_ingest_active: boolean | null;
  rescheduled: boolean;
  // LUM-844. `false` when a reconcile was needed and the scheduler could not be
  // updated: the column write has still committed, and re-sending the same
  // `active` repairs the schedule. Optional-safe against an older backend —
  // treat `undefined` as `true`.
  schedule_synced?: boolean;
  // Echoed when `active` was supplied: the value the server read back after
  // reconciling, which wins over the request if it lost a race.
  active?: boolean;
  // Echoed back for the fields the request supplied, so the client need not
  // refetch. Absent for fields it did not.
  name?: string;
  category?: string;
  poll_interval?: number;
}

export interface EmailSignalRow {
  signal_id: string;
  source_id: string;
  title: string;
  url: string;
  category: string;
  category_label: string;
  scope: string;
  published_at: string | null;
  content_summary: string;
  // LUM-642: rich-render fields (all returned by GET /signals; optional-safe
  // against an older backend).
  from_addr?: string;
  from_display?: string;
  importance_score?: number;
  relevance_score?: number;
  notified?: boolean;
  source_label?: string | null;
  created_at?: string | null;
  // LUM-643: durable per-caller triage state (null → untriaged → default lane
  // derivation). published_from enables a future owner/projection dedupe.
  triage_status?: TriageStatus | null;
  snoozed_until?: string | null;
  published_from?: string | null;
  // LUM-659: keyset cursor for triaged-set pagination (hand the last row's value
  // back as `triagedBefore`).
  triage_updated_at?: string | null;
  // LUM-649: per-caller feedback-learned relevance nudge, added to importance_score
  // for lane placement. Optional-safe against an older backend; deriveLane uses
  // `?? 0` so its absence is a no-op.
  learned_relevance_delta?: number;
}

// LUM-643: the four durable triage states (mirrors the backend CHECK + frozenset).
export type TriageStatus = "needs_you" | "good_to_know" | "snoozed" | "done";

export function createEmailSource(
  client: ApiClient,
  body: EmailSourceCreateRequest,
): Promise<EmailSourceCreateResponse> {
  return client.postJson("/me/email-sources", body);
}

export function putImapCredentials(
  client: ApiClient,
  sourceId: string,
  payload: Record<string, unknown>,
): Promise<void> {
  const q = new URLSearchParams({ instance: sourceId });
  return client.putJson(`/api/v1/me/connector-credentials/imap?${q}`, { payload });
}

// LUM-758: the registry response also says whether a mailbox added right now will
// actually start syncing on this deployment, or whether an operator still has to
// act. Optional-safe against an older backend (absent → falsy → no warning).
export interface ProvidersResponse {
  providers: ProviderInfo[];
  new_mailbox_needs_operator?: boolean;
  operator_action_reason?: string | null;
}

export function listEmailProviders(client: ApiClient): Promise<ProvidersResponse> {
  return client.getJson("/api/v1/me/email-providers");
}

export interface EmailTestCredentials {
  host: string;
  port?: number;
  username: string;
  password: string;
  use_ssl?: boolean;
  auth_type?: "app_password";
}

export function testEmailSource(
  client: ApiClient,
  creds: EmailTestCredentials,
): Promise<EmailProbeResponse> {
  return client.postJson("/me/email-sources/test", creds);
}

export function listSources(client: ApiClient): Promise<{ sources: SourceRow[]; total: number }> {
  return client.getJson("/sources");
}

export function syncSourceNow(client: ApiClient, sourceId: string): Promise<{ status: string; source_id: string }> {
  return client.postJson(`/sources/${encodeURIComponent(sourceId)}/sync`, {});
}

// LUM-701 — remove a mailbox. Ingested signals are kept (historical record);
// only the source row + its credentials/background footprint are removed.
export function deleteSource(
  client: ApiClient,
  sourceId: string,
): Promise<{ status: string; source_id: string }> {
  return client.delete(`/sources/${encodeURIComponent(sourceId)}`);
}

// LUM-639: toggle an IMAP mailbox between metadata and full-ingest mode.
// LUM-837 widened this into the GENERAL source-edit call — `name`, `category`
// and `poll_interval` apply to every source type; `full_ingest` stays IMAP-only.
// It is deliberately the ONE client for `PATCH /sources/{id}`: a second wrapper
// beside it is the divergent-copies trap, and this one is already live in
// `EmailSignalsView`.
//
// `url` is not accepted — changing it is delete-and-recreate (the poll cursor,
// the detected type and the extraction method all derive from the original).
export interface SourcePatch {
  full_ingest?: boolean;
  name?: string;
  category?: string;
  poll_interval?: number;
  // LUM-844 — pause/resume. Non-IMAP only; IMAP answers 422 `pause_not_supported`.
  active?: boolean;
}

export function updateSource(
  client: ApiClient,
  sourceId: string,
  patch: SourcePatch,
): Promise<UpdateSourceResponse> {
  return client.patchJson(`/sources/${encodeURIComponent(sourceId)}`, patch);
}

export interface SignalsPage {
  signals: EmailSignalRow[];
  total: number;
  // LUM-659: present on a triaged=set page when more triaged rows exist; pass it
  // back as `triagedBefore` to fetch the next (older) page.
  next_before?: string | null;
}

export function listSignals(
  client: ApiClient,
  params?: { source_id?: string; source_type?: string; limit?: number; triaged?: "set"; triagedBefore?: string },
): Promise<SignalsPage> {
  const q = new URLSearchParams();
  if (params?.source_id) q.set("source_id", params.source_id);
  // LUM-642: source_type=imap scopes the household union to email signals.
  if (params?.source_type) q.set("source_type", params.source_type);
  // LUM-643: triaged=set returns the caller's full triaged set, recency-independent
  // (so Done/Snoozed items older than the recency window are never lost).
  if (params?.triaged) q.set("triaged", params.triaged);
  // LUM-659: keyset pagination cursor for the triaged set.
  if (params?.triagedBefore) q.set("triaged_before", params.triagedBefore);
  if (params?.limit) q.set("limit", String(params.limit));
  const suffix = q.toString();
  return client.getJson(suffix ? `/signals?${suffix}` : "/signals");
}

// LUM-643: durable triage mutations. Bare `/signals/{id}/triage` (the signals
// router is mounted WITHOUT an /api/v1 prefix — GET /signals is bare too).
export interface TriageResponse {
  signal_id: string;
  status: TriageStatus;
  snoozed_until: string | null;
  updated_at: string | null;
}

export function setTriage(
  client: ApiClient,
  signalId: string,
  status: TriageStatus,
  snoozedUntil?: string,
): Promise<TriageResponse> {
  return client.patchJson(`/signals/${encodeURIComponent(signalId)}/triage`, {
    status,
    ...(snoozedUntil ? { snoozed_until: snoozedUntil } : {}),
  });
}

export function clearTriage(client: ApiClient, signalId: string): Promise<void> {
  return client.delete(`/signals/${encodeURIComponent(signalId)}/triage`);
}

// LUM-696 — bulk triage. Replaces N single-signal PATCHes with one request.
//
// This number is an API CONTRACT, not a tuning knob: the server rejects a larger
// batch with a 422, so the client chunks against it. Source of truth is
// `services.signal_triage.MAX_BULK_SIGNALS` — the two must agree, and there is
// no shared generator, so change both together.
export const MAX_BULK_TRIAGE = 200;

export interface BulkTriageResult {
  signal_id: string;
  status: TriageStatus | null;
  snoozed_until: string | null;
  updated_at: string | null;
}

export interface BulkTriageResponse {
  /** Every id the caller can SEE — including a cleared row that never had one
   *  (idempotent, exactly like the single-signal DELETE). */
  updated: BulkTriageResult[];
  /** Requested but not visible to the caller. Never "had no triage row". */
  skipped: string[];
  count: number;
}

/** Set (or, with `status: null`, clear) triage on up to `MAX_BULK_TRIAGE` ids. */
export function bulkTriage(
  client: ApiClient,
  signalIds: string[],
  status: TriageStatus | null,
  snoozedUntil?: string | null,
): Promise<BulkTriageResponse> {
  return client.postJson("/signals/triage/bulk", {
    signal_ids: signalIds,
    status,
    ...(snoozedUntil ? { snoozed_until: snoozedUntil } : {}),
  });
}

// LUM-645 — opt-in mailbox organization.
export interface EmailOrganizeUpdate {
  organize_label: boolean;
  organize_folder: boolean;
  organize_folder_categories?: string[];
  apply_to_existing?: boolean;
  /** LUM-729 — sent only after the user has seen the starvation warning. Omitted
   *  on a first attempt, which is what makes the gate mean anything. */
  acknowledge_external_consumer?: boolean;
}

export type UndoScope = "label" | "folder" | "all";

export interface OrganizeLogEntry {
  signal_url: string;
  op: "label" | "move";
  target: string | null;
  label_kind: string | null;
  applied_at: string;
  reversed_at: string | null;
}

export function setEmailOrganize(
  client: ApiClient,
  sourceId: string,
  body: EmailOrganizeUpdate,
): Promise<{ updated: boolean; apply_to_existing: boolean }> {
  return client.patchJson(`/me/email-sources/${encodeURIComponent(sourceId)}/organize`, body);
}

// LUM-650 — "let Lumogis send as me" opt-in (SMTP send). There is no `from` —
// From is always the mailbox's authenticated account (anti-spoofing). Host/port
// come from the provider registry for a known provider; an "Other" mailbox must
// supply smtp_host/smtp_port.
export interface EmailSmtpUpdate {
  enabled: boolean;
  reuse_imap_password?: boolean;
  app_password?: string;
  smtp_host?: string;
  smtp_port?: number;
}

export interface EmailSmtpResponse {
  ok: boolean;
  enabled: boolean;
  tested: boolean;
  error?: string | null;
}

export function setEmailSmtp(
  client: ApiClient,
  sourceId: string,
  body: EmailSmtpUpdate,
): Promise<EmailSmtpResponse> {
  return client.putJson(`/me/email-sources/${encodeURIComponent(sourceId)}/smtp`, body);
}

export function undoEmailOrganize(
  client: ApiClient,
  sourceId: string,
  scope: UndoScope = "all",
): Promise<{ undo_pending: boolean; scope: UndoScope }> {
  return client.postJson(
    `/me/email-sources/${encodeURIComponent(sourceId)}/organize/undo`,
    { scope },
  );
}

export function getEmailOrganizeLog(
  client: ApiClient,
  sourceId: string,
): Promise<{
  entries: OrganizeLogEntry[];
  /** LUM-729 follow-up — set when the undo sweep hit its tick bound and stopped
   *  trying. A bound that gave up invisibly would be the same silent failure
   *  this feature exists to stop. */
  undo_gave_up_at?: string | null;
  undo_unreversed?: number;
}> {
  return client.getJson(`/me/email-sources/${encodeURIComponent(sourceId)}/organize/log`);
}

// LUM-646 — chunked inbox cleanup.
export type CleanupState =
  | "pending"
  | "backfilling"
  | "ready"
  | "executing"
  | "undoing"
  | "done"
  | "failed";

export interface CleanupProposal {
  category: string;
  label: string | null;
  count: number;
  label_visible: boolean;
  can_move: boolean;
}

export interface CleanupJob {
  state: CleanupState;
  kind: "window" | "spam";
  progress: number;
  window?: { start: string | null; end: string | null };
  counts?: { scanned: number; classified: number; labeled: number; executed: number };
  proposals?: CleanupProposal[];
  spam_count?: number;
}

export interface CleanupSpamStatus {
  spam_count: number;
  trash_available: boolean;
}

export type CleanupUndoOp = "label" | "move" | "trash";

function cleanupBase(sourceId: string): string {
  return `/me/email-sources/${encodeURIComponent(sourceId)}/cleanup`;
}

export function startCleanup(
  client: ApiClient,
  sourceId: string,
  window: { window_start?: string | null; window_end?: string | null },
): Promise<{ job_id: string; state: CleanupState }> {
  return client.postJson(cleanupBase(sourceId), window);
}

export function getCleanupJob(
  client: ApiClient,
  sourceId: string,
  jobId: string,
): Promise<CleanupJob> {
  return client.getJson(`${cleanupBase(sourceId)}/${encodeURIComponent(jobId)}`);
}

export function approveCleanup(
  client: ApiClient,
  sourceId: string,
  jobId: string,
  actions: { category: string; move: boolean }[],
  /** LUM-729 — sent only after the user has seen the starvation warning. */
  acknowledgeExternalConsumer = false,
): Promise<{ approved: number }> {
  return client.postJson(`${cleanupBase(sourceId)}/${encodeURIComponent(jobId)}/approve`, {
    actions,
    ...(acknowledgeExternalConsumer ? { acknowledge_external_consumer: true } : {}),
  });
}

export function undoCleanup(
  client: ApiClient,
  sourceId: string,
  jobId: string,
  ops: CleanupUndoOp[] = ["label", "move", "trash"],
): Promise<{ undo_pending: boolean }> {
  return client.postJson(`${cleanupBase(sourceId)}/${encodeURIComponent(jobId)}/undo`, { ops });
}

export function getCleanupSpam(
  client: ApiClient,
  sourceId: string,
): Promise<CleanupSpamStatus> {
  return client.getJson(`/me/email-sources/${encodeURIComponent(sourceId)}/cleanup-spam`);
}

export function startCleanupSpam(
  client: ApiClient,
  sourceId: string,
): Promise<{ job_id: string; state: CleanupState }> {
  return client.postJson(`/me/email-sources/${encodeURIComponent(sourceId)}/cleanup-spam`, {});
}
