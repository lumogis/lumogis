// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
// LUM-646 — chunked inbox cleanup: pick a date range, watch Lumogis classify + label it,
// then batch-approve moving categories into folders. A separate "empty spam to Trash"
// action operates on the provider's Junk folder. Every action is reversible (job-scoped
// undo). Move-to-Trash is confined to the spam folder — the window pass never trashes
// inbox mail.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { useAuth } from "../../auth/AuthProvider";
import { ApiError } from "../../api/client";
import { starvationWarning, type StarvationWarning } from "./externalConsumerWarning";
import {
  approveCleanup,
  getCleanupJob,
  getCleanupSpam,
  startCleanup,
  startCleanupSpam,
  undoCleanup,
  type CleanupJob,
  type CleanupState,
} from "../../api/emailSources";

export interface EmailCleanupPassProps {
  sourceId: string;
  /** From the provider registry: does this provider display Lumogis labels? */
  labelVisible: boolean;
}

const TERMINAL: CleanupState[] = ["done", "failed"];

function isTerminal(state?: CleanupState): boolean {
  return state !== undefined && TERMINAL.includes(state);
}

export function EmailCleanupPass({ sourceId, labelVisible }: EmailCleanupPassProps): JSX.Element {
  const { client } = useAuth();
  const qc = useQueryClient();
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [jobId, setJobId] = useState<string | null>(null);
  const [moveCats, setMoveCats] = useState<string[]>([]);
  const [msg, setMsg] = useState<string | null>(null);
  const [starvation, setStarvation] = useState<StarvationWarning | null>(null);

  const onErr = (e: unknown) =>
    setMsg(e instanceof ApiError ? "Something went wrong — try again." : "Failed");

  const jobQ = useQuery<CleanupJob>({
    queryKey: ["email-cleanup", sourceId, jobId],
    queryFn: () => getCleanupJob(client, sourceId, jobId as string),
    enabled: !!jobId,
    refetchInterval: (q) => (isTerminal(q.state.data?.state) ? false : 1500),
  });

  // Lazy: checking the spam folder opens an IMAP session, so only fetch when the user
  // asks — never eagerly on mount (this panel lives in a <details>, so it renders for
  // every mailbox on the email page).
  const spamQ = useQuery({
    queryKey: ["email-cleanup-spam", sourceId],
    queryFn: () => getCleanupSpam(client, sourceId),
    enabled: false,
  });

  const job = jobQ.data;
  const jobActive = !!jobId && !isTerminal(job?.state);

  const startM = useMutation({
    mutationFn: () =>
      startCleanup(client, sourceId, {
        window_start: start || null,
        window_end: end || null,
      }),
    onSuccess: (r) => {
      setJobId(r.job_id);
      setMsg("Working through that date range — this can take a little while.");
    },
    onError: onErr,
  });

  const approveM = useMutation({
    // LUM-729: this is the second way to move mail out of INBOX, and it 422s the
    // same way the organize route does when paperless is configured. Without
    // this branch the user would meet "Something went wrong — try again" on a
    // refusal that trying again can never clear.
    mutationFn: (acknowledged: boolean = false) =>
      approveCleanup(
        client,
        sourceId,
        jobId as string,
        moveCats.map((category) => ({ category, move: true })),
        acknowledged,
      ),
    onSuccess: (r) => {
      setStarvation(null);
      setMsg(
        r.approved > 0
          ? "Filing those into folders now — reversible with Undo."
          : "Nothing selected to move. Labels are already applied.",
      );
      void qc.invalidateQueries({ queryKey: ["email-cleanup", sourceId, jobId] });
    },
    onError: (e: unknown) => {
      const warn = starvationWarning(e);
      if (warn) {
        setStarvation(warn);
        setMsg(null);
        return;
      }
      onErr(e);
    },
  });

  const undoM = useMutation({
    mutationFn: () => undoCleanup(client, sourceId, jobId as string),
    onSuccess: () => setMsg("Undo started — putting everything back."),
    onError: onErr,
  });

  const spamM = useMutation({
    mutationFn: () => startCleanupSpam(client, sourceId),
    onSuccess: (r) => {
      setJobId(r.job_id);
      setMsg("Emptying your spam folder to Trash — reversible until your provider clears Trash.");
    },
    onError: onErr,
  });

  const proposals = useMemo(() => job?.proposals ?? [], [job?.proposals]);
  const progressPct = Math.round((job?.progress ?? 0) * 100);

  const toggleMove = (slug: string) => {
    // An acknowledgement is of a specific set of categories; changing the set
    // invalidates it (LUM-729).
    setStarvation(null);
    setMoveCats((cur) => (cur.includes(slug) ? cur.filter((s) => s !== slug) : [...cur, slug]));
  };

  return (
    <section aria-labelledby="cleanup-heading" data-testid="email-cleanup-pass">
      <h3 id="cleanup-heading">Clean up my inbox</h3>
      <p>
        Work through a messy inbox one date range at a time. Lumogis labels the mail it finds;
        you approve which categories to file into folders. Everything is reversible.
      </p>

      <div className="lumogis-cleanup__range">
        <label>
          From{" "}
          <input
            type="date"
            aria-label="Start date"
            value={start}
            onChange={(e) => setStart(e.target.value)}
          />
        </label>
        <label>
          To{" "}
          <input
            type="date"
            aria-label="End date"
            value={end}
            onChange={(e) => setEnd(e.target.value)}
          />
        </label>
        <button
          type="button"
          onClick={() => startM.mutate()}
          disabled={startM.isPending || jobActive}
        >
          Start cleanup
        </button>
      </div>

      {jobId && job && (
        <div data-testid="cleanup-progress">
          <progress value={progressPct} max={100} />
          <span>
            {job.kind === "spam" ? "Emptying spam" : "Processing"} — {job.state} ({progressPct}%)
          </span>
          {job.counts && (
            <span data-testid="cleanup-counts">
              {" "}
              · {job.counts.classified} classified, {job.counts.executed} filed
            </span>
          )}
        </div>
      )}

      {job?.kind === "window" && job.state === "ready" && proposals.length > 0 && (
        <fieldset data-testid="cleanup-proposals">
          <legend>What Lumogis found — pick categories to file into folders</legend>
          {!labelVisible && (
            <p role="note" data-testid="cleanup-label-caveat">
              Your provider may not show Lumogis labels in its own app — filing into folders
              makes the organization visible.
            </p>
          )}
          {proposals.map((p) => (
            <label key={p.category}>
              <input
                type="checkbox"
                checked={moveCats.includes(p.category)}
                disabled={!p.can_move}
                onChange={() => toggleMove(p.category)}
              />{" "}
              {p.label ?? p.category} ({p.count})
            </label>
          ))}
          <button
            type="button"
            onClick={() => approveM.mutate(false)}
            disabled={approveM.isPending}
          >
            File selected into folders
          </button>
          {starvation && (
            <div
              role="alertdialog"
              aria-labelledby="cleanup-starve-heading"
              data-testid="cleanup-starvation-warning"
            >
              <h4 id="cleanup-starve-heading">This will hide mail from paperless</h4>
              <p>{starvation.message}</p>
              <p>{starvation.recovery}</p>
              <button
                type="button"
                data-testid="cleanup-starvation-continue"
                onClick={() => approveM.mutate(true)}
                disabled={approveM.isPending}
              >
                I understand — file them anyway
              </button>
              <button type="button" onClick={() => setStarvation(null)}>
                Cancel
              </button>
            </div>
          )}
        </fieldset>
      )}

      {jobId && job && job.state !== "undoing" && (
        <button type="button" onClick={() => undoM.mutate()} disabled={undoM.isPending}>
          Undo this cleanup
        </button>
      )}

      <div data-testid="cleanup-spam">
        {!spamQ.data && (
          <button type="button" onClick={() => void spamQ.refetch()} disabled={spamQ.isFetching}>
            Check spam folder
          </button>
        )}
        {spamQ.data && !spamQ.data.trash_available && (
          <p role="note">Your provider doesn’t expose a Trash folder, so spam clearing isn’t available here.</p>
        )}
        {spamQ.data?.trash_available && (
          <>
            <p>
              Spam folder: {spamQ.data.spam_count} message
              {spamQ.data.spam_count === 1 ? "" : "s"}.
            </p>
            <button
              type="button"
              onClick={() => spamM.mutate()}
              disabled={spamM.isPending || jobActive}
              title={jobActive ? "Finish the current cleanup first" : undefined}
            >
              Empty spam folder → Trash
            </button>
          </>
        )}
      </div>

      {msg && <p role="status">{msg}</p>}
    </section>
  );
}
