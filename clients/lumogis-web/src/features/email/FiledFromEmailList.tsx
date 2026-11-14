// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-702 — "Filed from email": what the attachment sweep put in the library,
// and how to take it back.
//
// The list shows SKIPS as well as filings, which is the point of the surface. A
// user who forwarded themselves a scanned invoice and cannot find it needs to
// know the sweep looked at it and could not read it — an absence with no
// explanation reads as the feature being broken.
//
// Undo is optimistic and reverts on failure. The server deletes the vectors
// BEFORE marking the row reversed, so a failure genuinely means nothing was
// removed, and putting the row back is the honest thing to show.

import { useCallback, useEffect, useState } from "react";

import type { ApiClient } from "../../api/client";
import {
  explainSkip,
  fetchFiledAttachments,
  isRefilable,
  isUndecidable,
  isUndoable,
  refileAttachment,
  REFILE_EXPLANATIONS,
  undoDecline,
  undoFiledAttachment,
  type FiledAttachmentRow,
} from "../../api/emailAttachmentIngest";

export interface FiledFromEmailListProps {
  client: ApiClient;
  sourceId?: string;
}

function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${Math.round(n / 1024)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

function formatWhen(iso: string): string {
  if (!iso) return "";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}

export function FiledFromEmailList({
  client,
  sourceId,
}: FiledFromEmailListProps): JSX.Element {
  const [rows, setRows] = useState<FiledAttachmentRow[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [status, setStatus] = useState("");
  const [pending, setPending] = useState<Set<number>>(new Set());
  // Off by default: a list of everything you said no to is noise on a surface
  // about what was filed. On request, because the review list promises the undo
  // lives here — and a promise that points at a hidden row is not a promise.
  const [showDeclined, setShowDeclined] = useState(false);

  const load = useCallback(
    (abort?: AbortSignal) => {
      fetchFiledAttachments(client, sourceId, abort, showDeclined)
        .then((res) => {
          setRows(res.rows);
          setLoaded(true);
        })
        .catch(() => setLoaded(true));
    },
    [client, sourceId, showDeclined],
  );

  useEffect(() => {
    const ctrl = new AbortController();
    load(ctrl.signal);
    return () => ctrl.abort();
  }, [load]);

  const onUndo = useCallback(
    (row: FiledAttachmentRow) => {
      const before = rows;
      setPending((p) => new Set(p).add(row.id));
      // Optimistic: drop it now, restore the whole list if the server disagrees.
      setRows((rs) => rs.filter((r) => r.id !== row.id));
      undoFiledAttachment(client, row.id)
        .then((res) => {
          if (res.reversed_count === 0) {
            setRows(before);
            setStatus(`Could not remove ${row.filename} — try again`);
            return;
          }
          setStatus(`Removed ${row.filename} from your library`);
        })
        .catch(() => {
          setRows(before);
          setStatus(`Could not remove ${row.filename} — try again`);
        })
        .finally(() =>
          setPending((p) => {
            const next = new Set(p);
            next.delete(row.id);
            return next;
          }),
        );
    },
    [client, rows],
  );

  const onRefile = useCallback(
    (row: FiledAttachmentRow) => {
      setPending((p) => new Set(p).add(row.id));
      // NOT optimistic, unlike undo. Re-filing re-runs the fetch and extraction,
      // and the message may have been deleted since — showing the row restored
      // before the server agrees would be the same lie undo avoids in reverse.
      refileAttachment(client, row.id)
        .then((res) => {
          if (res.outcome === "refiled") {
            setRows((rs) =>
              rs.map((r) => (r.id === row.id ? { ...r, reversed_at: null } : r)),
            );
            setStatus(`${row.filename} is back in your library`);
            return;
          }
          setStatus(REFILE_EXPLANATIONS[res.outcome]);
        })
        .catch(() => setStatus(`Could not restore ${row.filename} — try again`))
        .finally(() =>
          setPending((p) => {
            const next = new Set(p);
            next.delete(row.id);
            return next;
          }),
        );
    },
    [client],
  );

  const onUndoDecline = useCallback(
    (row: FiledAttachmentRow) => {
      setPending((p) => new Set(p).add(row.id));
      // Not optimistic: undoing a decline DELETES the verdict row, so the row
      // does not change state — it ceases to exist and reappears in the review
      // list. Reloading is the honest way to show that.
      undoDecline(client, row.signal_id, row.part_index)
        .then((res) => {
          if (!res.restored) {
            setStatus(`Could not put ${row.filename} back — try again`);
            return;
          }
          setRows((rs) => rs.filter((r) => r.id !== row.id));
          setStatus(`${row.filename} is back in your review list`);
        })
        .catch(() => setStatus(`Could not put ${row.filename} back — try again`))
        .finally(() =>
          setPending((p) => {
            const next = new Set(p);
            next.delete(row.id);
            return next;
          }),
        );
    },
    [client],
  );

  const declinedToggle = (
    <label>
      <input
        type="checkbox"
        checked={showDeclined}
        onChange={(e) => setShowDeclined(e.target.checked)}
        aria-label="Show attachments you chose not to file"
      />
      Show attachments you chose not to file
    </label>
  );

  if (!loaded) {
    return <p data-testid="filed-loading">Loading…</p>;
  }

  if (rows.length === 0) {
    return (
      <section aria-label="Filed from email">
        {status ? (
          <p role="status" data-testid="filed-status">
            {status}
          </p>
        ) : null}
        <p data-testid="filed-empty">Nothing has been filed from email yet.</p>
        {/* Kept in the empty state: this is exactly where a user who just
            declined everything arrives looking for the undo. */}
        {declinedToggle}
      </section>
    );
  }

  return (
    <section aria-label="Filed from email">
      {status ? (
        <p role="status" data-testid="filed-status">
          {status}
        </p>
      ) : null}
      {declinedToggle}
      <ul data-testid="filed-list">
        {rows.map((row) => {
          const skip = explainSkip(row.skipped_reason);
          return (
            <li key={row.id} data-testid={`filed-row-${row.id}`}>
              <span data-testid="filed-filename">{row.filename}</span>
              {/* The question this surface answers is "where did this come
                  from?" — `invoice.pdf` alone answers it for nobody. */}
              {row.from_addr || row.from_display ? (
                <span data-testid="filed-from">
                  from {row.from_display || row.from_addr}
                </span>
              ) : null}
              {row.subject ? (
                <span data-testid="filed-subject">{row.subject}</span>
              ) : null}
              <span data-testid="filed-size">{formatBytes(row.size_bytes)}</span>
              <time dateTime={row.ingested_at}>{formatWhen(row.ingested_at)}</time>
              {skip ? (
                <span data-testid="filed-skip-reason">{skip}</span>
              ) : row.reversed_at ? (
                <span data-testid="filed-reversed">Removed</span>
              ) : null}
              {/* No Undo on a skip: nothing was ever filed, so the button would
                  promise an action that does nothing. */}
              {isUndoable(row) ? (
                <button
                  type="button"
                  onClick={() => onUndo(row)}
                  disabled={pending.has(row.id)}
                  aria-label={`Undo filing of ${row.filename}`}
                >
                  Undo
                </button>
              ) : null}
              {/* The sweep will never re-file this on its own — undo is sticky by
                  design, so changing your mind needs an explicit action. */}
              {isRefilable(row) ? (
                <button
                  type="button"
                  onClick={() => onRefile(row)}
                  disabled={pending.has(row.id)}
                  aria-label={`File ${row.filename} again`}
                >
                  File again
                </button>
              ) : null}
              {/* A decline is the ONE skip the user can take back — they made
                  that call. Every other reason is the sweep's verdict about the
                  file itself, and re-offering it would ask an unanswerable
                  question again. */}
              {isUndecidable(row) ? (
                <button
                  type="button"
                  onClick={() => onUndoDecline(row)}
                  disabled={pending.has(row.id)}
                  aria-label={`Reconsider ${row.filename}`}
                >
                  Reconsider
                </button>
              ) : null}
            </li>
          );
        })}
      </ul>
    </section>
  );
}
