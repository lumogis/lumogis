// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// "Delete this entry" confirmation (LUM-966). Owner-only; the caller
// (EntityCardPanel) decides when to offer it.
//
// Delete removes the entry, its mentions, links, merge records and household copy,
// and puts its names on the member's list of names Lumogis won't learn, so a re-read
// cannot bring it back. The dialog says so up front. It is an AlertDialog: Cancel comes
// before Delete in the DOM, so focus starts on it. Delete does not close the dialog by
// itself; the caller navigates away once the server has answered.

import { useEffect, useState } from "react";

import type { ApiClient } from "../../api/client";
import {
  deleteEntity,
  getEntityHistory,
  parseEntityEditError,
  type EntityCard,
  type EntityDeleteResponse,
  type EntityEditError,
} from "../../api/search";
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
import { kindNoun, withArticle } from "./EntityKindDialog";

export interface EntityDeleteDialogProps {
  card: EntityCard;
  client: ApiClient;
  onClose: () => void;
  /** Called with the server's answer after a successful delete. */
  onDeleted: (response: EntityDeleteResponse) => void;
}

export function EntityDeleteDialog({
  card,
  client,
  onClose,
  onDeleted,
}: EntityDeleteDialogProps): JSX.Element {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<EntityEditError | "generic" | null>(null);
  // An open merge into this entry cannot be undone once the entry is gone. A failed
  // history read only omits that line: the delete itself stays allowed.
  const [hasOpenMerge, setHasOpenMerge] = useState(false);

  useEffect(() => {
    const ctrl = new AbortController();
    getEntityHistory(client, card.entity_id, ctrl.signal)
      .then((res) => {
        if (ctrl.signal.aborted) return;
        setHasOpenMerge(
          res.items.some((i) => i.direction === "merged_in" && i.undone_at === null),
        );
      })
      .catch(() => {
        /* the line is a courtesy; its absence never blocks the delete */
      });
    return () => ctrl.abort();
  }, [client, card.entity_id]);

  const noun = withArticle(kindNoun(card.type));

  const onConfirm = async (): Promise<void> => {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      const res = await deleteEntity(client, card.entity_id, card.version ?? 1);
      onDeleted(res);
    } catch (err) {
      setError(parseEntityEditError(err) ?? "generic");
      setBusy(false);
    }
  };

  return (
    <AlertDialog open onOpenChange={(open) => (open ? undefined : onClose())}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Delete “{card.name}”?</AlertDialogTitle>
          <AlertDialogDescription>
            Removed from your list, search and chat. Lumogis won&apos;t learn “{card.name}” as {noun}{" "}
            again. Your documents are not changed.
          </AlertDialogDescription>
        </AlertDialogHeader>
        {card.share_status === "shared" && (
          <p className="m-0 text-sm text-muted-foreground">Also removed for your household.</p>
        )}
        {hasOpenMerge && (
          <p className="m-0 text-sm text-muted-foreground">
            Merges into this entry can no longer be undone.
          </p>
        )}
        {error !== null && <DeleteError error={error} />}
        <AlertDialogFooter>
          <AlertDialogCancel disabled={busy}>Cancel</AlertDialogCancel>
          <AlertDialogAction
            variant="destructiveSolid"
            onClick={() => void onConfirm()}
            disabled={busy}
          >
            {busy ? "Deleting…" : "Delete"}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}

function DeleteError({ error }: { error: EntityEditError | "generic" }): JSX.Element {
  let text = "Couldn't delete. Try again.";
  if (error !== "generic") {
    if (error.code === "version_conflict") {
      text = "This entry changed since you opened it. Close and try again.";
    } else if (error.code === "store_unavailable") {
      text = "Couldn't delete right now — try again.";
    } else if (error.code === "not_owner") {
      text = "Only the owner can delete this. You can hide it from your list.";
    } else if (error.code === "entity_not_found") {
      text = "This entry is already gone.";
    }
  }
  return (
    <Alert variant="destructive">
      <AlertDescription>{text}</AlertDescription>
    </Alert>
  );
}
