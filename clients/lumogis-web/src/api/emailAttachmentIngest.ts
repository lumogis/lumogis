// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Client for the LUM-702 attachment auto-ingest surface.
// Mirrors orchestrator/routes/api_v1/email_attachment_ingest.py.
//
// The filed list deliberately carries SKIPS alongside filings. A skipped row is
// how the UI explains an absence — "no text could be read from this file type"
// is a far better answer than a document the user expected and cannot find.

import type { ApiClient } from "./client";

/** Reasons the sweep declined to file an attachment. A row with one of these is
 *  a decision, not a failure — the sweep will not reconsider that part. */
export type SkipReason =
  | "no_extractor"
  | "blocked_ext"
  | "too_large"
  | "no_text"
  | "duplicate"
  | "message_gone"
  | "ambiguous"
  // LUM-727. Unlike its siblings this is the USER's decision, not the sweep's —
  // which is why it is the only one that can be taken back (`undoDecline`).
  | "declined";

export interface FiledAttachmentRow {
  id: number;
  signal_id: string;
  part_index: number;
  filename: string;
  mime_type: string;
  size_bytes: number;
  /** `email-attachment://{source}/{signal}/{part}` — a STABLE scheme. Search
   *  results, document detail and purge all key on it. */
  logical_path: string;
  ingested_at: string;
  /** Where it came from. Empty when the signal has since been purged — the
   *  ledger keeps no FK on signal_id, matching `email_attachment`. */
  from_addr: string;
  from_display: string;
  /** LUM-742: the mailbox this was filed from. No name here — unlike the
   *  candidates path the server has no `sources` join, so the page resolves it
   *  from the sources listing it already holds. */
  source_id?: string;
  subject: string;
  /** Non-null once the user has taken this filing back. */
  reversed_at: string | null;
  skipped_reason: SkipReason | null;
}

export interface FiledAttachmentsResponse {
  rows: FiledAttachmentRow[];
  total: number;
}

export interface UndoResponse {
  reversed_count: number;
}

/** Outcome of a re-file attempt (LUM-721). `not_found` deliberately covers
 *  "not yours", "never filed" and "not currently reversed" alike. */
export type RefileOutcome = "refiled" | "not_found" | "duplicate" | "unavailable";

export interface RefileResponse {
  outcome: RefileOutcome;
}

/** What the user is told after a re-file that did not put the document back. */
export const REFILE_EXPLANATIONS: Record<Exclude<RefileOutcome, "refiled">, string> = {
  not_found: "That filing is no longer available to restore",
  duplicate: "Already filed — the same file arrived in another message",
  unavailable: "Couldn't read it again — the original email may have been deleted",
};

/** A reversed filing can be put back; a skip never created a document, so there
 *  is nothing to restore. Undo is deliberately sticky (the sweep will not
 *  re-file on its own), which is exactly why this action has to exist. */
export function isRefilable(row: FiledAttachmentRow): boolean {
  return row.skipped_reason === null && row.reversed_at !== null;
}

export interface AttachmentIngestSettings {
  enabled: boolean;
  /** Widens the sweep back over existing mail instead of go-forward only. */
  apply_to_existing?: boolean;
}

/** What the user is told when the sweep declined. Phrased as an explanation of
 *  an absence, not as an error — none of these is something they did wrong. */
export const SKIP_EXPLANATIONS: Record<SkipReason, string> = {
  no_extractor: "No text could be read from this file type",
  blocked_ext: "Not filed (executable file type)",
  too_large: "Too large to file automatically",
  no_text: "No text could be read",
  duplicate: "Already filed",
  message_gone: "The original message is no longer in the mailbox",
  ambiguous: "Couldn't identify the message this came from",
  declined: "You chose not to file this",
};

export function explainSkip(reason: SkipReason | null): string | null {
  if (!reason) return null;
  return SKIP_EXPLANATIONS[reason] ?? "Not filed";
}

/** A declined row is the one skip the user can take back — they made that
 *  decision, so they can unmake it. Every other reason is the sweep's verdict
 *  about the file itself, and re-offering it would just ask an unanswerable
 *  question again. */
export function isUndecidable(row: FiledAttachmentRow): boolean {
  return row.skipped_reason === "declined";
}

/** Only a full-ingest mailbox has attachments to file — a metadata-mode mailbox
 *  never downloaded message bodies, so enabling the toggle would be a silent
 *  no-op the user reads as a broken feature. The server answers 422; the UI uses
 *  this to disable the control and explain first. */
export function canFileAttachments(extractionMethod: string): boolean {
  return extractionMethod === "imap_full_ingest";
}

export const METADATA_MODE_EXPLANATION =
  "This mailbox only stores message headers, so its attachments were never " +
  "downloaded. Switch it to full ingest to review them for your library.";

/** A row is undoable only when it actually filed something and has not already
 *  been reversed. Skips never created a document, so there is nothing to take
 *  back — offering the button would promise an action that does nothing. */
export function isUndoable(row: FiledAttachmentRow): boolean {
  return row.skipped_reason === null && row.reversed_at === null;
}

export async function fetchFiledAttachments(
  client: ApiClient,
  sourceId?: string,
  abort?: AbortSignal,
  /** LUM-727. Declined rows are hidden by DEFAULT — a list of everything the
   *  user said no to is noise on a surface about what was filed. But the review
   *  list tells them a decline can be undone from here, so the surface has to be
   *  able to show them on request, or that promise points nowhere. */
  includeDeclined = false,
): Promise<FiledAttachmentsResponse> {
  const qs = new URLSearchParams();
  if (sourceId) qs.set("source_id", sourceId);
  if (includeDeclined) qs.set("include_declined", "true");
  const suffix = qs.toString() ? `?${qs}` : "";
  return client.getJson<FiledAttachmentsResponse>(
    `/api/v1/email/attachment-ingest/filed${suffix}`,
    { signal: abort },
  );
}

/** Undo ONE filing. */
export async function undoFiledAttachment(
  client: ApiClient,
  logId: number,
  abort?: AbortSignal,
): Promise<UndoResponse> {
  return client.postJson<{ log_id: number }, UndoResponse>(
    "/api/v1/email/attachment-ingest/undo",
    { log_id: logId },
    { signal: abort },
  );
}

/** Undo every active filing for a mailbox. */
export async function undoAllForSource(
  client: ApiClient,
  sourceId: string,
  abort?: AbortSignal,
): Promise<UndoResponse> {
  return client.postJson<{ source_id: string }, UndoResponse>(
    "/api/v1/email/attachment-ingest/undo",
    { source_id: sourceId },
    { signal: abort },
  );
}

export async function setAttachmentIngest(
  client: ApiClient,
  sourceId: string,
  settings: AttachmentIngestSettings,
  abort?: AbortSignal,
): Promise<{ updated: boolean; apply_to_existing: boolean }> {
  return client.patchJson<
    AttachmentIngestSettings,
    { updated: boolean; apply_to_existing: boolean }
  >(
    `/api/v1/email/sources/${encodeURIComponent(sourceId)}/attachment-ingest`,
    settings,
    { signal: abort },
  );
}

/** Put back a filing the user previously undid. */
export async function refileAttachment(
  client: ApiClient,
  logId: number,
  abort?: AbortSignal,
): Promise<RefileResponse> {
  return client.postJson<{ log_id: number }, RefileResponse>(
    "/api/v1/email/attachment-ingest/refile",
    { log_id: logId },
    { signal: abort },
  );
}

// ---------------------------------------------------------------------------
// Candidates — the per-attachment decision (LUM-727)
// ---------------------------------------------------------------------------
//
// LUM-702 filed everything from an enabled mailbox and offered undo as the
// safety net. That assumes filing everything is acceptable BECAUSE undo exists,
// which only holds when most attachments are worth keeping. They are not, so
// nothing is filed now without an explicit accept or a matching rule.
//
// Only `acceptCandidates` costs anything. Listing and declining are pure
// database work over rows that already exist, which is what makes reviewing a
// large, mostly-uninteresting stream affordable.

export interface AttachmentCandidate {
  signal_id: string;
  part_index: number;
  filename: string;
  mime_type: string;
  size_bytes: number;
  from_addr: string;
  from_display: string;
  subject: string;
  /** Taxonomy SLUG, never the label — labels are user-editable (LUM-629). */
  category: string;
  received_at: string;
  /** LUM-742: the mailbox this arrived in. A filing rule belongs to exactly one
   *  mailbox, so without this a cross-mailbox list has no rule "Always file from
   *  this sender" could create — which is why that button used to disappear. */
  source_id: string;
  source_name: string;
}

/** A mailbox named in a response for a reason the user can act on. */
export interface SourceRef {
  source_id: string;
  source_name: string;
}

/** Grouped by sender, biggest group first: deciding once for whoever sends most
 *  is the single largest reduction in the size of the list. */
export interface CandidateGroup {
  from_addr: string;
  from_display: string;
  /** LUM-742: half the group key. Two mailboxes receiving the same sender are
   *  two groups, because one rule cannot mean both. */
  source_id: string;
  source_name: string;
  count: number;
  candidates: AttachmentCandidate[];
}

/** The composite identity of a group. Used for React keys and test ids alike:
 *  keying on `from_addr` alone duplicates keys the moment the same sender
 *  appears in two mailboxes, which is exactly the case this feature exists for. */
export function groupKey(g: Pick<CandidateGroup, "from_addr" | "source_id">): string {
  return `${g.source_id}:${g.from_addr}`;
}

export interface CandidatesResponse {
  groups: CandidateGroup[];
  total: number;
  /** More candidates exist than were returned. Reported, never silent — a user
   *  who cannot see the rest must at least know there is a rest. */
  truncated: boolean;
  /** Attachments the mailbox can no longer produce. Terminal by design — the
   *  bytes are unfetchable, so re-offering them would ask an unanswerable
   *  question — but terminal must not mean invisible. Without this the
   *  attachment simply stops appearing and the user concludes nothing works. */
  unreachable: number;
  /** LUM-742: every pending attachment across the caller's mailboxes, unbounded
   *  by `limit`. NOT `total`, which is the page length after trimming and can
   *  never exceed the limit — a badge reading that would show 1 forever. */
  pending_total: number;
  /** Mailboxes with rows beyond their quota. The whole user-facing mitigation
   *  for a mailbox not being fully represented: without it, "your window filled
   *  up" is indistinguishable from "nothing pending here". */
  truncated_sources: SourceRef[];
  /** Mailboxes whose query failed this request. */
  errored_sources: SourceRef[];
}

/** Said in the review list, where the user is actually waiting for the file. */
export function explainUnreachable(n: number): string {
  return (
    `${n} attachment${n === 1 ? "" : "s"} can no longer be opened — the original ` +
    `message${n === 1 ? " was" : "s were"} moved or deleted in your mailbox.`
  );
}

export interface AcceptResponse {
  accepted: number;
  /** What was not filed, and why. `over_limit` means the bulk ceiling was hit
   *  and those refs still need a second pass. */
  skipped: Array<{ signal_id: string; part_index: number; reason: string }>;
}

/** A stable key for one attachment. Composite because neither half is unique on
 *  its own — one message carries several parts, and part 0 exists in every
 *  message. */
export function candidateKey(c: { signal_id: string; part_index: number }): string {
  return `${c.signal_id}:${c.part_index}`;
}

/** What the user is told about attachments an accept did not file. */
export const ACCEPT_SKIP_EXPLANATIONS: Record<string, string> = {
  not_found: "No longer available",
  over_limit: "Not reached this time — select fewer and try again",
  unavailable: "Couldn't read it — the original email may have been deleted",
  duplicate: "Already in your library",
  blocked_ext: "Not filed (executable file type)",
  too_large: "Too large to file",
  no_extractor: "No text could be read from this file type",
  no_text: "No text could be read",
};

export function explainAcceptSkip(reason: string): string {
  return ACCEPT_SKIP_EXPLANATIONS[reason] ?? "Not filed";
}

export async function fetchCandidates(
  client: ApiClient,
  opts: { sourceId?: string; limit?: number } = {},
  abort?: AbortSignal,
): Promise<CandidatesResponse> {
  const qs = new URLSearchParams();
  if (opts.sourceId) qs.set("source_id", opts.sourceId);
  if (opts.limit) qs.set("limit", String(opts.limit));
  const suffix = qs.toString() ? `?${qs}` : "";
  return client.getJson<CandidatesResponse>(
    `/api/v1/email/attachment-ingest/candidates${suffix}`,
    { signal: abort },
  );
}

/** File the picked attachments. **The only call here that spends IMAP and
 *  embedding**, and it does so synchronously — the server bounds the batch and
 *  reports the remainder in `skipped` rather than truncating silently. */
export async function acceptCandidates(
  client: ApiClient,
  refs: Array<{ signal_id: string; part_index: number }>,
  abort?: AbortSignal,
): Promise<AcceptResponse> {
  return client.postJson<{ refs: typeof refs }, AcceptResponse>(
    "/api/v1/email/attachment-ingest/candidates/accept",
    { refs },
    { signal: abort },
  );
}

/** Say no. Costs nothing and is terminal — the part is never offered again
 *  unless `undoDecline` puts it back. */
export async function declineCandidates(
  client: ApiClient,
  refs: Array<{ signal_id: string; part_index: number }>,
  abort?: AbortSignal,
): Promise<{ declined: number }> {
  return client.postJson<{ refs: typeof refs }, { declined: number }>(
    "/api/v1/email/attachment-ingest/candidates/decline",
    { refs },
    { signal: abort },
  );
}

export async function undoDecline(
  client: ApiClient,
  signalId: string,
  partIndex: number,
  abort?: AbortSignal,
): Promise<{ restored: boolean }> {
  return client.postJson<
    { signal_id: string; part_index: number },
    { restored: boolean }
  >(
    "/api/v1/email/attachment-ingest/candidates/undo-decline",
    { signal_id: signalId, part_index: partIndex },
    { signal: abort },
  );
}

// ---------------------------------------------------------------------------
// Rules — "always file this", so the list shrinks toward empty
// ---------------------------------------------------------------------------

export type RuleMatchType = "sender" | "category";

export interface AttachmentRule {
  id: string;
  match_type: RuleMatchType;
  /** A lowercased address for `sender`; a taxonomy SLUG for `category`. */
  match_value: string;
  created_at: string;
  /** A category rule naming a slug that no longer exists. It cannot fire, and
   *  showing it as if it worked is how a user concludes the feature is broken.
   *  Not swept automatically — they deleted a category, not an instruction. */
  stale: boolean;
}

export const STALE_RULE_EXPLANATION =
  "This category no longer exists, so the rule can't file anything. Delete it " +
  "or recreate the category.";

/** Deleting a rule stops FUTURE filing and changes nothing already filed —
 *  `filed_by_rule_id` is ON DELETE SET NULL, so those documents keep their undo
 *  path. Said out loud in the UI, because "delete rule" reads like a retraction
 *  and the confirm copy has to correct that. */
export const DELETE_RULE_EXPLANATION =
  "Stops filing new attachments. Anything already filed stays in your library.";

export async function fetchAttachmentRules(
  client: ApiClient,
  sourceId: string,
  abort?: AbortSignal,
): Promise<{ rules: AttachmentRule[] }> {
  return client.getJson<{ rules: AttachmentRule[] }>(
    `/api/v1/email/attachment-rules?source_id=${encodeURIComponent(sourceId)}`,
    { signal: abort },
  );
}

export interface PaperlessOverlap {
  overlap: boolean;
  confidence: "high" | "low" | null;
  status: "ok" | "auth" | "unavailable";
  message: string | null;
}

/** LUM-728 — advisory only; the POST gate is authoritative and recomputes.
 *  This exists because a `low` finding never gates, so the 422 can never carry
 *  it: without this call the advisory has no delivery path at all. */
export async function fetchPaperlessOverlap(
  client: ApiClient,
  sourceId: string,
  abort?: AbortSignal,
): Promise<PaperlessOverlap> {
  return client.getJson<PaperlessOverlap>(
    `/api/v1/email/attachment-ingest/paperless-overlap?source_id=${encodeURIComponent(sourceId)}`,
    { signal: abort },
  );
}

/** One entry per mailbox that HAS filing rules. See `fetchPaperlessOverlapAll`. */
export interface PaperlessOverlapItem extends PaperlessOverlap {
  source_id: string;
  source_name: string;
  rule_count: number;
}

/** LUM-744 — every rule-bearing mailbox, for one paperless config fetch.
 *
 *  Accounts and rules are per paperless *instance*; only the matching is
 *  per-source. Asking per mailbox re-downloads the whole config each time, and
 *  `AttachmentRuleList` is mounted once per mailbox card — so a per-source call
 *  from there means one fetch per mailbox, which is the thing this removes.
 *
 *  `context` picks the tense of `message`, server-side. The rules page shows
 *  rules that already exist, so it wants `existing`; the default here matches
 *  the route's own default rather than restating it. Copy is never assembled
 *  client-side: `paperlessOverlapWarning` matches on `code`, not prose,
 *  specifically so the server can reword freely.
 *
 *  Mailboxes with no rules are ABSENT — they file nothing, so they cannot
 *  duplicate anything. Absence is not an error. */
export async function fetchPaperlessOverlapAll(
  client: ApiClient,
  context: "creating" | "existing" = "existing",
  abort?: AbortSignal,
): Promise<PaperlessOverlapItem[]> {
  const res = await client.getJson<{ items?: PaperlessOverlapItem[] }>(
    `/api/v1/email/attachment-ingest/paperless-overlap/all?context=${encodeURIComponent(context)}`,
    { signal: abort },
  );
  return res?.items ?? [];
}

export async function createAttachmentRule(
  client: ApiClient,
  sourceId: string,
  matchType: RuleMatchType,
  matchValue: string,
  abort?: AbortSignal,
  acknowledgeOverlap = false,
): Promise<AttachmentRule> {
  return client.postJson<
    {
      source_id: string;
      match_type: RuleMatchType;
      match_value: string;
      acknowledge_paperless_overlap: boolean;
      acknowledged_source_id: string | null;
    },
    AttachmentRule
  >(
    "/api/v1/email/attachment-rules",
    {
      source_id: sourceId,
      match_type: matchType,
      match_value: matchValue,
      acknowledge_paperless_overlap: acknowledgeOverlap,
      // LUM-743: name the mailbox the acknowledgement is about. Derived from
      // `sourceId` rather than taken as a separate argument on purpose — a
      // caller cannot then pass a consent for one mailbox while creating a rule
      // on another, which is precisely the mistake being designed out. The
      // server treats a mismatch as no acknowledgement and asks again.
      acknowledged_source_id: acknowledgeOverlap ? sourceId : null,
    },
    { signal: abort },
  );
}

export async function deleteAttachmentRule(
  client: ApiClient,
  ruleId: string,
  abort?: AbortSignal,
): Promise<void> {
  await client.delete(`/api/v1/email/attachment-rules/${encodeURIComponent(ruleId)}`, {
    signal: abort,
  });
}
