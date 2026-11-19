// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-837 chunk A — the sources list. `POST /sources` was the only one of five
// source routes with no caller anywhere in the web app, so nothing could manage
// a Paperless server, an RSS feed or a watched page from a browser.
//
// This chunk ships list / sync / delete against routes that already exist. The
// add flow and its two consent arms are chunk B.
//
// Mounted under `/admin` for now because `POST /sources` is the one source verb
// that is `require_admin` — `GET`, `PATCH` and `DELETE` are all per-user and
// owner-scoped. LUM-839 moves the whole view to per-user, so nothing here
// hard-codes "admin" in copy or logic: that should be a remount, not a rewrite.
//
// LUM-844: pause/resume is a row action, not an edit field, and a paused row
// hides Sync now because `POST /sources/{id}/sync` refuses it (409 source_paused).
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";

import { ApiError } from "../../api/client";
import { useAuth } from "../../auth/AuthProvider";
import {
  deleteSource,
  listSources,
  syncSourceNow,
  updateSource,
  type SourceRow,
} from "../../api/sources";
import { EmptyState } from "../../components/page/EmptyState";
import { ErrorState } from "../../components/page/ErrorState";
import { LoadingPlaceholder, SkeletonText } from "../../components/page/LoadingState";
import { PageSection } from "../../components/page/PageSection";
import { RowActions } from "../../components/page/RowActions";
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
import { formatDateTime } from "../../util/formatTimestamp";
import { useSettingsHeading } from "../settings/settingsHeading";
import { AddSourcePanel } from "./AddSourcePanel";
import { EditSourceRow } from "./EditSourceRow";

// Mailboxes are deliberately absent. They have a management surface already
// (the email settings page), and `delete_source` on an IMAP row additionally
// tears down cleanup jobs and removes the IMAP/SMTP credentials — a second,
// less-explained path to that is a footgun.
const MANAGED_TYPES = new Set(["paperless", "rss", "page"]);

// `GET /sources` shares its cache with the email settings page, which reads the
// same response. One key, so a delete here cannot leave that list stale.
const SOURCES_KEY = ["email", "sources"] as const;

// Plain words for the kind of source (LUM-993). `source_type` stays the API's
// word; this is only what a household member reads.
function kindLabel(sourceType: string): string {
  switch (sourceType) {
    case "paperless":
      return "Document archive";
    case "rss":
      return "Feed";
    case "page":
      return "Web page";
    default:
      return "Source";
  }
}

function everyLabel(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds <= 0) return "Checked on a schedule";
  if (seconds % 86400 === 0) {
    const d = seconds / 86400;
    return d === 1 ? "Checked every day" : `Checked every ${d} days`;
  }
  if (seconds % 3600 === 0) {
    const h = seconds / 3600;
    return h === 1 ? "Checked every hour" : `Checked every ${h} hours`;
  }
  const m = Math.max(1, Math.round(seconds / 60));
  return m === 1 ? "Checked every minute" : `Checked every ${m} minutes`;
}

// A sentence for each code the pollers record. Unknown codes fall back to a
// generic line: the server can ship a new code before this client knows it, and
// a raw code is not something a household member can act on.
function problemSentence(code: string): string {
  switch (code) {
    case "credential":
      return "Lumogis can't sign in. Check the address and key you saved for it.";
    case "network":
    case "egress_blocked":
      return "Lumogis couldn't reach it. This often clears up on its own.";
    default:
      return "The last check didn't work.";
  }
}

/**
 * What the row's health says.
 *
 * `last_polled_at` on its own is NOT health: every poll path advances it,
 * including the ones that failed. A paperless source with no credential is
 * caught, logged, and has its timestamp advanced anyway — so the timestamp
 * alone would assert "polled 2 minutes ago" for a source that has never worked
 * and never will. `last_poll_error_code` is what distinguishes them, which is
 * why LUM-837 also made the paperless and RSS/page pollers record it; until
 * then only IMAP did, and this branch could never have fired for any type this
 * view shows.
 */
function healthOf(row: SourceRow): { label: string; failing: boolean; paused: boolean } {
  // Paused wins over an error code. `_record_poll_health` only CLEARS an error
  // on a successful poll, and a paused source never polls again — so checking
  // the error first would leave a paused row shouting `role="alert"` forever,
  // and pausing a broken source (the obvious way to silence it) would not.
  if (!row.active) {
    return {
      label: row.last_poll_error_code
        ? `Paused. The last check had a problem: ${problemSentence(row.last_poll_error_code)}`
        : "Paused. Nothing new is checked until you resume it.",
      failing: false,
      paused: true,
    };
  }
  if (row.last_poll_error_code) {
    return {
      label: problemSentence(row.last_poll_error_code),
      failing: true,
      paused: false,
    };
  }
  return {
    label: row.last_polled_at ? `Last checked ${formatDateTime(row.last_polled_at)}` : "Not checked yet",
    failing: false,
    paused: false,
  };
}

export function SourcesView(): JSX.Element {
  const { client } = useAuth();
  const qc = useQueryClient();
  const [msg, setMsg] = useState<string | null>(null);
  const [confirming, setConfirming] = useState<string | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  // LUM-844: the pause/resume that did not take, kept so "Try again" can re-send
  // the same `active`. The row's toggle has already flipped by then, so the
  // toggle alone could not express "do the same thing again".
  const [retryVars, setRetryVars] = useState<{ id: string; active: boolean } | null>(null);
  // Every status message but the "did not take" one clears the retry offer, so a
  // stale Try again never outlives the message it belongs to.
  const say = (text: string) => {
    setMsg(text);
    setRetryVars(null);
  };

  // LUM-282. The onboarding handoff closes its modal by NAVIGATING here, and
  // `ModalFrame`'s focus restoration (`prev?.isConnected && prev.focus()`) was
  // written for same-page dismissal: after a route change the element it wants
  // to restore is disconnected, the guard fails, and focus falls to <body>. A
  // keyboard or screen-reader user would get no landing point at the most
  // consequential click in that flow.
  //
  // Gated on the navigation state so an ordinary visit to /settings/sources does not
  // steal focus — this only fires for the arrival the handoff creates.
  const navigate = useNavigate();
  const { pathname, state } = useLocation();
  const fromOnboarding = (state as { fromOnboarding?: boolean } | null)?.fromOnboarding === true;
  const focusLandedRef = useRef(false);

  // Scrub the flag once it has been used. `location.state` belongs to the
  // HISTORY ENTRY, not to the visit: leaving it set means that every later
  // return to this entry — Back from anywhere, forward again, a restored
  // session — remounts this view with a fresh `focusedRef` and the same
  // `fromOnboarding: true`, and yanks focus to the heading away from wherever
  // the user had put it. The handoff is a one-time arrival; the state should be
  // too. `replace` so this does not grow the history stack.
  useEffect(() => {
    if (fromOnboarding) navigate(pathname, { replace: true, state: null });
  }, [fromOnboarding, navigate, pathname]);
  // The heading is the Settings frame's (`SettingsPage` renders the one `<h1>`
  // for every page and shares its ref); rendered alone, there is no frame
  // heading and this does nothing.
  const frameHeading = useSettingsHeading();
  useEffect(() => {
    // Latch success, not attempt — otherwise a failed first try blocks later
    // retries.
    if (!fromOnboarding || focusLandedRef.current) return;
    // VERIFY, don't assume. When we arrive from the onboarding handoff the
    // modal that sent us is usually STILL MOUNTED: `navigate` runs on a
    // microtask after the completion resolves, while the gate only unmounts
    // the modal a macrotask later (query-core's notifyManager defers observer
    // notification). Until it does, `ModalFrame` holds `inert` on `#root` —
    // and an inert subtree cannot take focus, so this first call is a
    // spec-compliant no-op in a real browser. Focus would then sit on the
    // handoff button until that unmounts, and land on <body>.
    //
    // The lift and this retry are BOTH `setTimeout(0)` work — order is not
    // guaranteed — so one macrotask is not always enough. A short chain is.
    //
    // jsdom does NOT enforce inert (measured: focus succeeds inside an inert
    // subtree), so no unit test in this repo can catch that without mocking.
    // The Playwright case in `onboarding_dismiss_persists.spec.ts` is the one
    // that can.
    const tryFocus = (attemptsLeft: number) => {
      const node = frameHeading?.current;
      if (focusLandedRef.current || !node || !node.isConnected) return;
      node.focus();
      if (document.activeElement === node) {
        focusLandedRef.current = true;
        return;
      }
      if (attemptsLeft <= 0) return;
      window.setTimeout(() => tryFocus(attemptsLeft - 1), 0);
    };
    tryFocus(3);
  }, [fromOnboarding, frameHeading]);

  const q = useQuery({
    queryKey: SOURCES_KEY,
    queryFn: () => listSources(client),
  });

  // The 500 body carries an exception string (`Could not save source: {exc}`),
  // which can contain a DSN or a host. Never render `detail` from this surface.
  const onErr = () => say("Something went wrong — try again.");

  const delM = useMutation({
    mutationFn: (id: string) => deleteSource(client, id),
    onSuccess: () => {
      setConfirming(null);
      say("Source removed.");
      void qc.invalidateQueries({ queryKey: SOURCES_KEY });
    },
    onError: onErr,
  });

  const editM = useMutation({
    mutationFn: (vars: { id: string; patch: Parameters<typeof updateSource>[2] }) =>
      updateSource(client, vars.id, vars.patch),
    onSuccess: (res) => {
      setEditing(null);
      // `unchanged` means the values supplied already matched — saying "Saved"
      // there would claim a write that did not happen.
      say(res.status === "unchanged" ? "No changes to save." : "Source updated.");
      void qc.invalidateQueries({ queryKey: SOURCES_KEY });
    },
    onError: (e: unknown) =>
      say(
        e instanceof ApiError && e.status === 422
          ? "That doesn't look right — a name and category can't be empty, and the interval must be at least 60 seconds."
          : "Something went wrong — try again.",
      ),
  });

  // Separate from `editM`: its onSuccess closes the editor and says "Source
  // updated." / "No changes to save.", which is the wrong copy for a one-click
  // toggle, and the busy state must be per row.
  const pauseM = useMutation({
    mutationFn: (vars: { id: string; active: boolean }) =>
      updateSource(client, vars.id, { active: vars.active }),
    onSuccess: (res, vars) => {
      // The server's reconciled read wins over what we asked for: a request that
      // lost a race to another tab is worded, and retried, as the state it found.
      const now = res.active ?? vars.active;
      if (res.schedule_synced === false) {
        // The column is saved but the scheduler did not follow. Re-sending the
        // same `active` always reconciles, so Try again is safe.
        setMsg(
          now
            ? "Saved as resumed, but checking could not be restarted yet."
            : "Saved as paused, but Lumogis could not confirm it stopped checking.",
        );
        setRetryVars({ id: vars.id, active: now });
      } else {
        setMsg(
          now
            ? "Source resumed. Checking it now."
            : "Source paused. A check already in progress will finish; nothing new is checked until you resume it.",
        );
        setRetryVars(null);
      }
      void qc.invalidateQueries({ queryKey: SOURCES_KEY });
    },
    // Never render `detail`, matching `onErr`.
    onError: onErr,
  });

  const syncM = useMutation({
    mutationFn: (id: string) => syncSourceNow(client, id),
    onSuccess: () => say("Sync queued."),
    // 409 carries two meanings, told apart by `code`: the source is paused (a
    // stale list), or a manual sync is already queued. Both are true statements
    // about state, not failures worth a generic apology.
    onError: (e: unknown) =>
      say(
        e instanceof ApiError && e.code === "source_paused"
          ? "That source is paused — resume it to sync."
          : e instanceof ApiError && e.status === 409
            ? "A sync is already queued for that source."
            : "Something went wrong — try again.",
      ),
  });

  const rows = q.isSuccess
    ? (q.data?.sources ?? []).filter((r) => MANAGED_TYPES.has(r.source_type))
    : [];

  // One status line for both branches (zero state and list). Try again appears
  // only beside the "did not take" message it belongs to (LUM-844).
  const statusLine = msg ? (
    <p
      role="status"
      aria-label="Source list status"
      className="m-0 mb-4 flex max-w-2xl flex-wrap items-center gap-2 text-sm"
    >
      {msg}
      {retryVars ? (
        <Button
          type="button"
          size="sm"
          variant="outline"
          onClick={() => pauseM.mutate(retryVars)}
          disabled={pauseM.isPending}
        >
          Try again
        </Button>
      ) : null}
    </p>
  ) : null;

  const addPanel = (
    <AddSourcePanel
      onAdded={() => {
        say("Source added.");
        void qc.invalidateQueries({ queryKey: SOURCES_KEY });
      }}
    />
  );

  const removing = rows.find((r) => r.id === confirming) ?? null;

  let body: JSX.Element;
  if (q.isPending) {
    body = (
      <LoadingPlaceholder label="Loading your sources…" className="max-w-2xl">
        <SkeletonText lines={4} />
      </LoadingPlaceholder>
    );
  } else if (q.isError) {
    const err = q.error;
    body = (
      <ErrorState
        message={
          err instanceof ApiError && err.status === 401
            ? "You're not signed in, or your session has ended."
            : "Lumogis couldn't load your sources."
        }
        onRetry={() => void q.refetch()}
      />
    );
  } else if (rows.length === 0) {
    // LUM-282. The offer goes ABOVE the add form, because its job is to
    // introduce it — below, it answers a question the reader has already
    // given up on. It ABSORBS the standing intro paragraph rather than
    // stacking on it.
    //
    // No `actions`. `AddSourcePanel`'s credentials link is deliberately
    // `target="_blank"` because in-app navigation unmounts the panel
    // and takes the preview and any consent acknowledgements with it. An
    // `EmptyState` href action renders a same-tab <Link>. Two controls that
    // look identical while one preserves the user's work and the other
    // destroys it is worse than duplication — and every action needed is in
    // the panel directly below.
    //
    // The title names the missing CATEGORIES, never the state of the
    // account: `rows` is filtered by MANAGED_TYPES, so this branch is also
    // reached by someone with three mailboxes, for whom "you haven't
    // connected anything yet" would be false.
    body = (
      <>
        <EmptyState
          title="No document archives, feeds or watched pages yet"
          helperText="Lumogis can read a Paperless-ngx archive, an RSS feed, or a page you want watched."
        >
          <p className="m-0 text-sm text-muted-foreground">
            Paperless takes two steps: save its address and API token first, then add it
            as a source below. Mailboxes are set up on the email settings page.
          </p>
        </EmptyState>

        {statusLine}

        {addPanel}
      </>
    );
  } else {
    body = (
      <>
        {statusLine}

        <PageSection
          title="Your sources"
          description="Document archives, feeds and watched pages. Mailboxes are managed on the email settings page."
        >
          <ul role="list" className="m-0 flex list-none flex-col divide-y divide-border p-0">
            {rows.map((row) => {
              const health = healthOf(row);
              if (editing === row.id) {
                return (
                  <EditSourceRow
                    key={row.id}
                    row={row}
                    busy={editM.isPending}
                    onCancel={() => setEditing(null)}
                    onSave={(patch) => editM.mutate({ id: row.id, patch })}
                  />
                );
              }
              return (
                <li key={row.id} data-source-id={row.id} className="flex flex-col gap-2 py-3">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="min-w-0 flex-1 text-sm font-medium break-words">{row.name}</span>
                    <Badge variant="secondary">{kindLabel(row.source_type)}</Badge>
                    {health.paused ? <Badge variant="outline">Paused</Badge> : null}
                    {row.active ? (
                      <Button
                        type="button"
                        size="sm"
                        variant="outline"
                        onClick={() => syncM.mutate(row.id)}
                        // Scoped to this row: a slow sync on one source must not
                        // grey out every other row's controls.
                        disabled={syncM.isPending && syncM.variables === row.id}
                      >
                        Sync now
                      </Button>
                    ) : null}
                    <RowActions
                      label={`Actions for ${row.name}`}
                      items={[
                        {
                          label: row.active ? "Pause" : "Resume",
                          onSelect: () => pauseM.mutate({ id: row.id, active: !row.active }),
                          disabled: pauseM.isPending && pauseM.variables?.id === row.id,
                        },
                        { label: "Edit", onSelect: () => setEditing(row.id) },
                        { label: "Remove", destructive: true, onSelect: () => setConfirming(row.id) },
                      ]}
                    />
                  </div>
                  <p className="m-0 text-xs text-muted-foreground">
                    {everyLabel(row.poll_interval)} · Category: {row.category}
                  </p>
                  {health.failing ? (
                    <Alert variant="destructive">
                      <AlertDescription>{health.label}</AlertDescription>
                    </Alert>
                  ) : (
                    <p className="m-0 text-xs text-muted-foreground">{health.label}</p>
                  )}
                </li>
              );
            })}
          </ul>
        </PageSection>

        {addPanel}
      </>
    );
  }

  return (
    <section aria-busy={q.isPending ? true : undefined}>
      {body}

      <AlertDialog open={removing !== null} onOpenChange={(open) => (open ? undefined : setConfirming(null))}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{`Remove ${removing?.name ?? "this source"}?`}</AlertDialogTitle>
            <AlertDialogDescription>
              Lumogis stops checking it and forgets this source. You can add it again later.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              variant="destructiveSolid"
              disabled={delM.isPending}
              onClick={() => {
                if (removing) delM.mutate(removing.id);
                // Close first: a failed remove must leave the page (and its
                // status line) reachable, not hidden behind the dialog.
                setConfirming(null);
              }}
            >
              {`Remove ${removing?.name ?? ""}`.trim()}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </section>
  );
}
