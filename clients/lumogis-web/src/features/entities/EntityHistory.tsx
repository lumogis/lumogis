// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// "History" section on the entry page (LUM-965): the merges into this entry (made by
// the member, the review queue, an admin or automatic dedup) with **Undo merge**, and,
// after an undo, the mentions the entry gained since the merge so the member can move
// any that belong to the restored entry. Owner-only; hidden when there is nothing to show.
//
// Undo is newest first per entry: only the newest open merge shows an Undo button; an
// older one says which newer merge to undo first. Everything that decides is server-side.
/* eslint-disable react-refresh/only-export-components */

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import type { ApiClient } from "../../api/client";
import {
  getEntityHistory,
  moveMentions,
  parseEntityEditError,
  undoMerge,
  type EntityMergeHistoryItem,
  type EntityUndoMergeResponse,
} from "../../api/search";
import { ErrorState } from "../../components/page/ErrorState";
import { PageSection } from "../../components/page/PageSection";
import { Alert, AlertDescription } from "../../components/ui/alert";
import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogAction,
} from "../../components/ui/alert-dialog";
import { Button } from "../../components/ui/button";
import { Checkbox } from "../../components/ui/checkbox";
import { Label } from "../../components/ui/label";
import { formatMentionDate } from "./EntityMentions";

export interface EntityHistoryProps {
  entityId: string;
  client: ApiClient;
  isOwner?: boolean;
  /** Called after an undo or a mention move so the page can refresh its card and lists. */
  onChanged?: () => void;
}

const fmt = (iso: string): string => formatMentionDate(iso);

/** One line describing a history item. */
export function historyLine(item: EntityMergeHistoryItem): string {
  if (item.direction === "merged_out") {
    return `Was merged into “${item.other_name}” on ${fmt(item.merged_at)}${
      item.undone_at ? `, undone on ${fmt(item.undone_at)}` : ""
    }`;
  }
  const when = fmt(item.merged_at);
  const what =
    item.origin === "auto_dedup"
      ? `Merged automatically: “${item.other_name}” on ${when}`
      : item.origin === "review_queue"
        ? `Merged from the review queue: “${item.other_name}” on ${when}`
        : item.origin === "admin"
          ? `Merged by an admin: “${item.other_name}” on ${when}`
          : `Merged “${item.other_name}” into this entry on ${when}`;
  return item.undone_at ? `${what} — undone on ${fmt(item.undone_at)}` : what;
}

export function EntityHistory({
  entityId,
  client,
  isOwner = true,
  onChanged,
}: EntityHistoryProps): JSX.Element | null {
  const q = useQuery({
    queryKey: ["kg", "entities", "history", entityId],
    queryFn: ({ signal }) => getEntityHistory(client, entityId, signal),
    enabled: isOwner,
  });
  const [confirming, setConfirming] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [result, setResult] = useState<EntityUndoMergeResponse | null>(null);

  if (!isOwner) return null;
  if (q.isError) {
    return (
      <PageSection title="History">
        <ErrorState
          title="Couldn't load the history."
          message="Please try again in a moment."
          onRetry={() => void q.refetch()}
          doctorHint={false}
        />
      </PageSection>
    );
  }
  const items = q.data?.items ?? [];
  if (items.length === 0 && result === null) return null;
  const nameOf = (mergeId: string | null, fallback?: string | null): string =>
    items.find((i) => i.merge_id === mergeId)?.other_name ?? fallback ?? "the newer entry";

  const doUndo = async (item: EntityMergeHistoryItem): Promise<void> => {
    setBusy(true);
    setNotice(null);
    try {
      const res = await undoMerge(client, item.merge_id);
      setResult(res);
      setConfirming(null);
      onChanged?.();
    } catch (err) {
      const parsed = parseEntityEditError(err);
      if (parsed && parsed.code === "undo_newer_first") {
        setNotice(`Undo the newer merge of “${parsed.newerOtherName ?? "that entry"}” first.`);
      } else if (parsed && parsed.code === "already_undone") {
        setNotice("That merge was already undone.");
      } else if (parsed && (parsed.code === "merge_not_found" || parsed.code === "loser_id_taken")) {
        setNotice("This merge can't be undone any more.");
      } else {
        setNotice("Couldn't undo. Try again.");
      }
      setConfirming(null);
    } finally {
      setBusy(false);
      void q.refetch();
    }
  };

  const confirmingItem = items.find((i) => i.merge_id === confirming) ?? null;

  return (
    <div data-testid="entity-history">
      <PageSection title="History">
        {notice && (
          <Alert variant="destructive" className="mb-3">
            <AlertDescription>{notice}</AlertDescription>
          </Alert>
        )}
        {result && (
          <MovePanel
            client={client}
            result={result}
            onDone={() => {
              setResult(null);
              void q.refetch();
              onChanged?.();
            }}
          />
        )}
        <ul role="list" className="m-0 flex list-none flex-col divide-y divide-border p-0">
          {items.map((item) => (
            <li key={item.merge_id} className="flex min-h-11 flex-wrap items-center gap-2 py-1.5 text-sm">
              <span className="min-w-0 flex-1">{historyLine(item)}</span>
              {item.can_undo && (
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={() => setConfirming(item.merge_id)}
                >
                  Undo merge
                </Button>
              )}
              {!item.can_undo && !item.undone_at && item.direction === "merged_in" && (
                <span className="text-xs text-muted-foreground">
                  Undo the newer merge of “{nameOf(item.newer_merge_id)}” first.
                </span>
              )}
            </li>
          ))}
        </ul>
      </PageSection>
      <AlertDialog
        open={confirmingItem !== null}
        onOpenChange={(open) => (open || busy ? undefined : setConfirming(null))}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Undo this merge?</AlertDialogTitle>
            <AlertDialogDescription>
              “{confirmingItem?.other_name}” comes back as its own entry with its documents and
              links. Lumogis won&apos;t merge the two again.
            </AlertDialogDescription>
          </AlertDialogHeader>
          {confirmingItem?.restores_sharing && (
            <p className="m-0 text-sm text-muted-foreground" data-testid="undo-sharing-line">
              “{confirmingItem.other_name}” will be shared with your household again, as it was
              before the merge.
            </p>
          )}
          <AlertDialogFooter>
            <AlertDialogCancel disabled={busy}>Cancel</AlertDialogCancel>
            <AlertDialogAction
              disabled={busy}
              onClick={() => confirmingItem && void doUndo(confirmingItem)}
            >
              {busy ? "Undoing…" : "Undo merge"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}

function MovePanel({
  client,
  result,
  onDone,
}: {
  client: ApiClient;
  result: EntityUndoMergeResponse;
  onDone: () => void;
}): JSX.Element {
  const { restored, survivor, mentions_since: mentions, skipped_rows: skipped } = result;
  const [picked, setPicked] = useState<Set<number>>(new Set());
  const [busy, setBusy] = useState(false);
  const [moved, setMoved] = useState<number | null>(null);
  const [error, setError] = useState(false);
  // Moving bumps both versions; keep the latest so a second move does not conflict.
  const [versions, setVersions] = useState({
    from: survivor.version ?? 1,
    to: restored.version ?? 1,
  });

  const toggle = (id: number): void =>
    setPicked((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const move = async (): Promise<void> => {
    if (busy || picked.size === 0) return;
    setBusy(true);
    setError(false);
    try {
      const res = await moveMentions(client, survivor.entity_id, {
        to_entity_id: restored.entity_id,
        relation_ids: [...picked],
        version: versions.from,
        to_version: versions.to,
      });
      setMoved(res.moved);
      setVersions({ from: res.source.version ?? versions.from, to: res.target.version ?? versions.to });
      setPicked(new Set());
    } catch {
      setError(true);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      className="mb-3 flex flex-col gap-2 rounded-lg border p-3 text-sm"
      role="group"
      aria-label="After undo"
    >
      <p className="m-0">“{restored.name}” is back as its own entry.</p>
      {skipped > 0 && (
        <p className="m-0" data-testid="undo-skipped-line">
          Some items were deleted since the merge and could not be put back.
        </p>
      )}
      {mentions.length > 0 && moved === null && (
        <>
          <p className="m-0">
            “{survivor.name}” has gained these since the merge. Move any that belong to “
            {restored.name}”:
          </p>
          <ul role="list" className="m-0 flex list-none flex-col gap-1.5 p-0">
            {mentions.map((m) => (
              <li key={m.relation_id} className="flex items-center gap-2">
                <Checkbox
                  id={`move-${m.relation_id}`}
                  checked={picked.has(m.relation_id)}
                  onCheckedChange={() => toggle(m.relation_id)}
                />
                <Label htmlFor={`move-${m.relation_id}`} className="font-normal">
                  {m.title}
                </Label>
              </li>
            ))}
          </ul>
          <div>
            <Button type="button" disabled={busy || picked.size === 0} onClick={() => void move()}>
              Move to “{restored.name}”
            </Button>
          </div>
        </>
      )}
      {moved !== null && (
        <p role="status" className="m-0">
          Moved {moved} {moved === 1 ? "item" : "items"} to “{restored.name}”.
        </p>
      )}
      {error && (
        <Alert variant="destructive">
          <AlertDescription>Couldn&apos;t move. Reload the page and try again.</AlertDescription>
        </Alert>
      )}
      <div>
        <Button type="button" variant="outline" onClick={onDone}>
          Done
        </Button>
      </div>
    </div>
  );
}
