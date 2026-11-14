// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useAuth } from "../../auth/AuthProvider";
import { ApiError } from "../../api/client";
import {
  deleteSource,
  listEmailProviders,
  listSignals,
  listSources,
  syncSourceNow,
  updateSource,
  type ProviderInfo,
  type SourceRow,
} from "../../api/emailSources";
import { hasSmtpCredential, listMyCredentials } from "../../api/connectorCredentials";
import { getTaxonomy, type CategoryItem } from "../../api/meEmailLabels";
import { formatAuditTimestamp } from "../../util/formatTimestamp";
import { AttachmentIngestToggle } from "../email/AttachmentIngestToggle";
import { AttachmentRuleList } from "../email/AttachmentRuleList";
import { SubscriptionsView } from "../email/SubscriptionsView";
import { EmailCleanupPass } from "./EmailCleanupPass";
import { EmailMailboxSetupForm } from "./EmailMailboxSetupForm";
import { EmailOrganizeSettings } from "./EmailOrganizeSettings";
import { MailboxSendControl } from "./MailboxSendControl";
import { deriveSendState, type SendState } from "./sendState";
import { SignalListItem } from "./SignalListItem";
import { pollHealthMessage } from "./pollHealthMessage";

// LUM-849: the autonomy page. Path, not label — the label changed and the path
// deliberately did not (breaking a bookmarked security page is not reversible).
const AUTONOMY_SURFACE = "/me/permissions";

// LUM-645: does this mailbox's provider display Lumogis labels? Resolve the source's
// IMAP host against the provider registry; unknown ("Other") defaults to false so the
// UI conservatively steers to folder-sort.
function labelVisibleForSource(url: string, providers: ProviderInfo[]): boolean {
  let host = "";
  try {
    host = new URL(url).hostname;
  } catch {
    host = "";
  }
  const match = providers.find((p) => p.imap_host === host);
  return match?.label_visible ?? false;
}

export function EmailSignalsView(): JSX.Element {
  const { client } = useAuth();
  const qc = useQueryClient();
  const [selectedCategory, setSelectedCategory] = useState<string | null>(null);
  const [syncMsg, setSyncMsg] = useState<string | null>(null);

  // LUM-769: send state is the existence of an `smtp` credential row, so it comes
  // from a DIFFERENT endpoint than everything else on the card. One request for
  // the whole list, never one per mailbox — the `AttachmentIngestToggle` shape.
  const credentialsQ = useQuery({
    queryKey: ["me", "connector-credentials"],
    queryFn: () => listMyCredentials(client),
  });

  // The egress soft-save — persisted, but the connect-test could not run — is NOT
  // derivable from the credential row, which is identical either way. It is what
  // *this session's mutation response* said, so the VIEW holds it: deriving it
  // from the query would let the invalidation fired by the control's own success
  // handler erase it one refetch later, and the one outcome most worth saying
  // would flash and vanish. It still does not survive a reload; that limit is
  // real and deliberate, since the alternative is persisting a verification flag.
  const [softSaved, setSoftSaved] = useState<Set<string>>(new Set());

  const sourcesQ = useQuery({
    queryKey: ["email", "sources"],
    queryFn: () => listSources(client),
  });

  const taxonomyQ = useQuery({
    queryKey: ["email-labels", "taxonomy"],
    queryFn: () => getTaxonomy(client),
  });

  const signalsQ = useQuery({
    queryKey: ["email", "signals"],
    queryFn: () => listSignals(client, { limit: 50 }),
  });

  const providersQ = useQuery({
    queryKey: ["email", "providers"],
    queryFn: () => listEmailProviders(client),
  });

  const imapSources = useMemo(
    () => (sourcesQ.data?.sources ?? []).filter((s: SourceRow) => s.source_type === "imap"),
    [sourcesQ.data?.sources],
  );

  const enabledCategories = useMemo(
    () => (taxonomyQ.data?.categories ?? []).filter((c: CategoryItem) => c.enabled),
    [taxonomyQ.data?.categories],
  );

  const visibleSignals = useMemo(() => {
    const rows = signalsQ.data?.signals ?? [];
    if (!selectedCategory) return rows;
    return rows.filter((s) => s.category === selectedCategory);
  }, [signalsQ.data?.signals, selectedCategory]);

  const syncM = useMutation({
    mutationFn: (sourceId: string) => syncSourceNow(client, sourceId),
    onSuccess: () => {
      setSyncMsg("Sync queued.");
      void qc.invalidateQueries({ queryKey: ["email", "signals"] });
    },
    onError: (e) => {
      setSyncMsg(e instanceof ApiError ? String(e.detail) : "Sync failed");
    },
  });

  const [deleteMsg, setDeleteMsg] = useState<string | null>(null);
  const deleteM = useMutation({
    mutationFn: (sourceId: string) => deleteSource(client, sourceId),
    onSuccess: () => {
      setDeleteMsg(null);
      void qc.invalidateQueries({ queryKey: ["email", "sources"] });
      // LUM-769: removing a mailbox destroys its SMTP credential row too, so the
      // credentials query is stale. Only the two flows that create or destroy an
      // SMTP row need this — `modeM` and the attachment toggle do not touch one.
      void qc.invalidateQueries({ queryKey: ["me", "connector-credentials"] });
    },
    onError: () => {
      setDeleteMsg("Couldn't remove the mailbox. Try again.");
    },
  });

  const [modeMsg, setModeMsg] = useState<string | null>(null);
  const modeM = useMutation({
    mutationFn: (vars: { id: string; fullIngest: boolean }) =>
      updateSource(client, vars.id, { full_ingest: vars.fullIngest }),
    onSuccess: (res) => {
      setModeMsg(
        res.extraction_method === "imap_full_ingest" && !res.full_ingest_active
          ? "Switched to full ingest (pending operator enable)."
          : `Switched to ${res.extraction_method === "imap_full_ingest" ? "full ingest" : "metadata"}.`,
      );
      void qc.invalidateQueries({ queryKey: ["email", "sources"] });
    },
    onError: () => {
      setModeMsg("Couldn't change mode.");
    },
  });

  return (
    <section className="lumogis-email-signals" data-testid="email-signals-view">
      <h2>Email signals</h2>
      <p>Connect an IMAP mailbox, sync mail, and review classified signals.</p>

      <EmailMailboxSetupForm
        onCreated={() => {
          void qc.invalidateQueries({ queryKey: ["email", "sources"] });
          // LUM-769: connecting a mailbox with "let Lumogis send as me" ticked is
          // the single most common way an SMTP row comes into existence. Without
          // this the new state line reads "off" for a mailbox whose sending was
          // just switched on, until something else happens to refetch.
          void qc.invalidateQueries({ queryKey: ["me", "connector-credentials"] });
        }}
      />

      {imapSources.length > 0 ? (
        <div className="lumogis-email-signals__sources">
          <h3>Your mailboxes</h3>
          {/* LUM-849: the autonomy signpost, and it is OUTSIDE the .map() below
              on purpose.
              Beside `MailboxSendControl` would be the natural place — that is
              where a person decides whether Lumogis may send from a mailbox at
              all — but that control renders once PER MAILBOX, and this link
              points at a setting that is per-CONNECTOR
              (`connector_permissions` is UNIQUE (user_id, connector); there is
              no instance column). Rendering it inside the loop would show it
              three times to someone with three mailboxes and quietly teach them
              the setting is per-mailbox, which is the exact inference LUM-849
              exists to prevent. A test asserts it appears once. */}
          <p className="lumogis-email-signals__autonomy-signpost">
            Whether Lumogis sends and files for you without asking is set on the{" "}
            <Link to={AUTONOMY_SURFACE}>What Lumogis may do</Link> page. It applies to
            all your mailboxes.
          </p>
          <ul>
            {imapSources.map((src) => {
              const isFullIngest = src.extraction_method === "imap_full_ingest";
              const pendingEnable = isFullIngest && src.full_ingest_active === false;
              // A failed OR STILL-LOADING credentials query means UNKNOWN, never
              // "off". Rendering "off" before the list arrives is a claim the
              // client cannot support, and it would invite a user to turn on
              // something already on — including one whose SMTP row uses a
              // standalone password, which a default reuse-IMAP enable would
              // overwrite. Every existing test in this file and in
              // EmailModeToggle.test.tsx exercises the error path, because none
              // of them knew about /connector-credentials until LUM-769.
              // LUM-774: the ordering is load-bearing and lives in `deriveSendState`,
              // where it can be tested directly — it only misbehaves when the
              // credential and the session flag disagree, which no rendered
              // fixture can reach through the UI.
              const sendState: SendState = deriveSendState({
                credentialsUnavailable: credentialsQ.isError || credentialsQ.isPending,
                hasSmtpCredential: hasSmtpCredential(credentialsQ.data?.items ?? [], src.id),
                softSaved: softSaved.has(src.id),
              });
              return (
                <li key={src.id}>
                  <span>{src.name}</span>{" "}
                  <span className="lumogis-email-signals__mode" data-testid={`mode-${src.id}`}>
                    Mode: {isFullIngest ? "Full ingest" : "Metadata"}
                    {pendingEnable ? " (pending operator enable)" : ""}
                  </span>{" "}
                  {/* LUM-819: metadata mode fetches no body, so nothing of this
                      mailbox's message text is stored and searching it can only
                      ever return subject and sender hits. Stated HERE, where the
                      mode is chosen — a limitation a user discovers through a
                      search that finds nothing reads as a broken feature, not as
                      the consequence of a setting they picked. */}
                  {!isFullIngest && (
                    <span
                      className="lumogis-email-signals__search-note"
                      data-testid={`search-note-${src.id}`}
                    >
                      Message text is not searchable in this mode — subject and
                      sender only.
                    </span>
                  )}{" "}
                  {/* LUM-690/633: this mailbox's last poll could not reach a usable
                      connection. The code says WHY, and the copy differs per code
                      because the right user action differs — telling someone to
                      re-enter a password that is fine sends them round a loop they
                      cannot win. Unknown codes fall back to the generic message:
                      the server can ship a new code before this client knows it. */}
                  {src.last_poll_error_code ? (
                    <p role="alert" className="lumogis-form-error" data-testid={`health-${src.id}`}>
                      ⚠ Couldn't sync
                      {src.last_poll_error_at
                        ? ` since ${formatAuditTimestamp(src.last_poll_error_at)}`
                        : ""}{" "}
                      — {pollHealthMessage(src.last_poll_error_code, src.auth_method)}
                    </p>
                  ) : null}
                  <button
                    type="button"
                    aria-label={`Switch ${src.name} to ${isFullIngest ? "metadata" : "full ingest"}`}
                    onClick={() => modeM.mutate({ id: src.id, fullIngest: !isFullIngest })}
                    disabled={modeM.isPending}
                  >
                    {isFullIngest ? "Switch to metadata" : "Switch to full ingest"}
                  </button>{" "}
                  <button
                    type="button"
                    aria-label={`Sync now ${src.name}`}
                    onClick={() => syncM.mutate(src.id)}
                    disabled={syncM.isPending}
                  >
                    Sync now
                  </button>
                  {/* LUM-769: the two always-visible state lines. The card could
                      not answer "what is Lumogis doing with this mailbox?" without
                      opening three disclosures; these two say it at a glance. They
                      sit OUTSIDE every <details> on purpose — inside one, they
                      would answer the question only for someone who already knew
                      to look. */}
                  <MailboxSendControl
                    client={client}
                    sourceId={src.id}
                    mailboxName={src.name}
                    state={sendState}
                    smtpHostRequired={src.smtp_host_required === true}
                    // LUM-777: no `isError` gate is needed. Enabling or disabling
                    // sending never changes a mailbox's auth method, so a stale
                    // sources query cannot disagree with the credentials query
                    // about it.
                    authMethod={src.auth_method ?? null}
                    onChange={({ softSaved: wasSoftSaved }) => {
                      // Track the soft-save BEFORE invalidating: the refetch this
                      // triggers recomputes state from the credential row, which
                      // cannot distinguish verified from soft-saved.
                      setSoftSaved((prev) => {
                        const next = new Set(prev);
                        if (wasSoftSaved) next.add(src.id);
                        else next.delete(src.id);
                        return next;
                      });
                      void qc.invalidateQueries({ queryKey: ["email", "sources"] });
                      void qc.invalidateQueries({ queryKey: ["me", "connector-credentials"] });
                    }}
                  />
                  {/* LUM-727 changed what attachment filing MEANS — nothing is
                      filed automatically; attachments are listed for review. So
                      this line must not say "filing", which would reinstate a
                      promise the control directly below it retracts. Coerced with
                      `=== true` (not given an "unknown" state like the send line)
                      because the route emits `bool(...)` unconditionally, so the
                      `?` in the type is defensive, not a reachable state. */}
                  <p className="lumogis-email-signals__attachment-state">
                    Attachments:{" "}
                    {src.attachment_ingest_enabled === true ? "listed for review" : "off"}
                  </p>
                  <details className="lumogis-email-signals__organize">
                    <summary>Organize this mailbox</summary>
                    <EmailOrganizeSettings
                      sourceId={src.id}
                      labelVisible={labelVisibleForSource(
                        src.url,
                        providersQ.data?.providers ?? [],
                      )}
                    />
                  </details>
                  <details className="lumogis-email-signals__cleanup">
                    <summary>Clean up this mailbox</summary>
                    <EmailCleanupPass
                      sourceId={src.id}
                      labelVisible={labelVisibleForSource(
                        src.url,
                        providersQ.data?.providers ?? [],
                      )}
                    />
                    {/* LUM-742 / LUM-739: LUM-697 shipped this and never routed
                        it. Its stated intent was "surface in cleanup", and this
                        is the cleanup surface. Per-mailbox for now — the
                        Gmail/Outlook pattern is one hub across the account, but
                        that needs a cross-source endpoint that does not exist. */}
                    <SubscriptionsView client={client} sourceId={src.id} />
                  </details>
                  {/* LUM-742: the attachment-filing opt-in and its standing
                      rules. Both are per-mailbox by contract — the toggle and
                      the rule list each require a sourceId — so they belong on
                      the mailbox card, not on the cross-mailbox review route.
                      LUM-727 rejected settings for the REVIEW LIST specifically
                      ("this is the moment the user knows the answer"); that
                      reasoning does not extend to configuration. */}
                  {/* LUM-776: was "File attachments from this mailbox", which
                      promised the behaviour LUM-727 removed — and it is the copy
                      MOST people read, because a disclosure summary is what you
                      see before deciding whether to open it, while the correction
                      ("Nothing is filed automatically") is inside. A summary names
                      a section; the verb belongs on the control within it, which
                      already says "Review this mailbox's attachments…". */}
                  <details className="lumogis-email-signals__attachments">
                    <summary>Attachments from this mailbox</summary>
                    <AttachmentIngestToggle
                      client={client}
                      sourceId={src.id}
                      extractionMethod={src.extraction_method ?? ""}
                      enabled={src.attachment_ingest_enabled === true}
                      onChange={() => {
                        void qc.invalidateQueries({ queryKey: ["email", "sources"] });
                      }}
                    />
                    <AttachmentRuleList client={client} sourceId={src.id} />
                  </details>
                  <button
                    type="button"
                    aria-label={`Remove mailbox ${src.name}`}
                    onClick={() => {
                      if (
                        window.confirm(
                          `Remove ${src.name}? This stops syncing and disconnects the mailbox. ` +
                            "Signals already synced are kept as a historical record. You can " +
                            "reconnect it fresh at any time using the form above.",
                        )
                      ) {
                        setDeleteMsg(null);
                        deleteM.mutate(src.id);
                      }
                    }}
                    disabled={deleteM.isPending}
                  >
                    Remove mailbox
                  </button>
                </li>
              );
            })}
          </ul>
          {modeMsg ? (
            <p role="status" data-testid="mode-msg">
              {modeMsg}
            </p>
          ) : null}
          {syncMsg ? (
            <p role="status" data-testid="sync-msg">
              {syncMsg}
            </p>
          ) : null}
          {deleteMsg ? (
            <p role="alert" data-testid="delete-msg">
              {deleteMsg}
            </p>
          ) : null}
        </div>
      ) : null}

      {enabledCategories.length > 0 ? (
        <div className="lumogis-email-signals__filters" role="group" aria-label="Category filters">
          <button
            type="button"
            aria-pressed={selectedCategory === null}
            onClick={() => setSelectedCategory(null)}
          >
            All
          </button>
          {enabledCategories.map((cat) => (
            <button
              key={cat.slug}
              type="button"
              aria-pressed={selectedCategory === cat.slug}
              data-category-slug={cat.slug}
              onClick={() => setSelectedCategory(cat.slug)}
            >
              {cat.label}
            </button>
          ))}
        </div>
      ) : null}

      <ul className="lumogis-email-signals__list">
        {visibleSignals.map((signal) => (
          <SignalListItem key={signal.signal_id} signal={signal} />
        ))}
      </ul>
      {signalsQ.isSuccess && visibleSignals.length === 0 ? <p>No signals yet.</p> : null}
    </section>
  );
}
