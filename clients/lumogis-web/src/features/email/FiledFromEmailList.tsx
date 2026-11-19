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

import { ErrorState } from "@/components/page/ErrorState";
import { EmptyState } from "@/components/page/EmptyState";
import { LoadingPlaceholder, SkeletonText } from "@/components/page/LoadingState";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import { formatDateTime } from "@/util/formatTimestamp";
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

export function FiledFromEmailList({
  client,
  sourceId,
}: FiledFromEmailListProps): JSX.Element {
  const [rows, setRows] = useState<FiledAttachmentRow[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [loadError, setLoadError] = useState(false);
  const [status, setStatus] = useState("");
  const [pending, setPending] = useState<Set<number>>(new Set());
  // Off by default: a list of everything you said no to is noise on a surface
  // about what was filed. On request, because the review list promises the undo
  // lives here — and a promise that points at a hidden row is not a promise.
  const [showDeclined, setShowDeclined] = useState(false);

  const load = useCallback(
    (abort?: AbortSignal) => {
      setLoadError(false);
      fetchFiledAttachments(client, sourceId, abort, showDeclined)
        .then((res) => {
          setRows(res.rows);
          setLoaded(true);
        })
        .catch(() => {
          // An aborted request is an unmount or a toggle, not a failure.
          if (abort?.aborted) return;
          setLoadError(true);
          setLoaded(true);
        });
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
    <Label className="gap-2 font-normal">
      <Checkbox
        checked={showDeclined}
        onCheckedChange={(checked) => setShowDeclined(checked)}
        aria-label="Show attachments you chose not to file"
      />
      Show attachments you chose not to file
    </Label>
  );

  if (!loaded) {
    return (
      <div data-testid="filed-loading">
        <LoadingPlaceholder label="Loading filed attachments">
          <SkeletonText lines={3} />
        </LoadingPlaceholder>
      </div>
    );
  }

  if (loadError) {
    return (
      <ErrorState
        title="Couldn't load filed attachments"
        message="Lumogis couldn't get the list of attachments filed from email."
        onRetry={() => load()}
      />
    );
  }

  if (rows.length === 0) {
    return (
      <section aria-label="Filed from email" className="flex flex-col gap-3">
        {status ? (
          <p className="text-sm" role="status" data-testid="filed-status">
            {status}
          </p>
        ) : null}
        <div data-testid="filed-empty">
          <EmptyState
            title="Nothing has been filed from email yet."
            helperText="Attachments you file from the review list will appear here."
          />
        </div>
        {/* Kept in the empty state: this is exactly where a user who just
            declined everything arrives looking for the undo. */}
        {declinedToggle}
      </section>
    );
  }

  return (
    <section aria-label="Filed from email" className="flex flex-col gap-3">
      {status ? (
        <p className="text-sm" role="status" data-testid="filed-status">
          {status}
        </p>
      ) : null}
      {declinedToggle}
      <ul className="m-0 flex list-none flex-col p-0" data-testid="filed-list">
        {rows.map((row) => {
          const skip = explainSkip(row.skipped_reason);
          return (
            <li
              key={row.id}
              className="flex flex-wrap items-center gap-x-3 gap-y-1 border-t py-2 text-sm first:border-t-0"
              data-testid={`filed-row-${row.id}`}
            >
              <span className="min-w-0 font-medium break-all" data-testid="filed-filename">
                {row.filename}
              </span>
              {/* The question this surface answers is "where did this come
                  from?" — `invoice.pdf` alone answers it for nobody. */}
              {row.from_addr || row.from_display ? (
                <span className="text-muted-foreground" data-testid="filed-from">
                  from {row.from_display || row.from_addr}
                </span>
              ) : null}
              {row.subject ? (
                <span className="min-w-0 text-muted-foreground" data-testid="filed-subject">
                  {row.subject}
                </span>
              ) : null}
              <span className="text-xs text-muted-foreground" data-testid="filed-size">
                {formatBytes(row.size_bytes)}
              </span>
              <time className="text-xs text-muted-foreground" dateTime={row.ingested_at}>
                {formatDateTime(row.ingested_at)}
              </time>
              {skip ? (
                <Badge variant="outline" data-testid="filed-skip-reason">
                  {skip}
                </Badge>
              ) : row.reversed_at ? (
                <Badge variant="secondary" data-testid="filed-reversed">
                  Removed
                </Badge>
              ) : null}
              <span className="ml-auto flex gap-2">
                {/* No Undo on a skip: nothing was ever filed, so the button would
                    promise an action that does nothing. */}
                {isUndoable(row) ? (
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    onClick={() => onUndo(row)}
                    disabled={pending.has(row.id)}
                    aria-label={`Undo filing of ${row.filename}`}
                  >
                    Undo
                  </Button>
                ) : null}
                {/* The sweep will never re-file this on its own — undo is sticky by
                    design, so changing your mind needs an explicit action. */}
                {isRefilable(row) ? (
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    onClick={() => onRefile(row)}
                    disabled={pending.has(row.id)}
                    aria-label={`File ${row.filename} again`}
                  >
                    File again
                  </Button>
                ) : null}
                {/* A decline is the ONE skip the user can take back — they made
                    that call. Every other reason is the sweep's verdict about the
                    file itself, and re-offering it would ask an unanswerable
                    question again. */}
                {isUndecidable(row) ? (
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    onClick={() => onUndoDecline(row)}
                    disabled={pending.has(row.id)}
                    aria-label={`Reconsider ${row.filename}`}
                  >
                    Reconsider
                  </Button>
                ) : null}
              </span>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
