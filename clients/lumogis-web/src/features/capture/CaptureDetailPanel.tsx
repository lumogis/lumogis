// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Capture inbox detail (LUM-606): edit (PATCH), delete (DELETE), and Commit to
// memory (POST …/index — labelled "Retry" for a failed capture). On commit or
// delete success the row leaves the inbox (onRemoved). Errors surfaced via the
// shared formatCaptureErrorMessage.

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { ApiError } from "../../api/client";
import type { ApiClient } from "../../api/client";
import {
  deleteCapture,
  formatCaptureErrorMessage,
  indexCapture,
  patchCapture,
  type CaptureListItem,
} from "../../api/captures";
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
import { Button } from "../../components/ui/button";
import { Card, CardContent } from "../../components/ui/card";
import { Input } from "../../components/ui/input";
import { Label } from "../../components/ui/label";
import { Textarea } from "../../components/ui/textarea";

function errMsg(e: unknown): string {
  if (e instanceof ApiError) return formatCaptureErrorMessage(e.status, e.detail);
  if (e instanceof Error) return e.message;
  return "Request failed";
}

export function CaptureDetailPanel({
  client,
  item,
  onRemoved,
  onClose,
}: {
  client: ApiClient;
  item: CaptureListItem;
  onRemoved: (id: string) => void;
  onClose: () => void;
}): JSX.Element {
  const qc = useQueryClient();
  const [title, setTitle] = useState(item.title ?? "");
  const [text, setText] = useState(item.text ?? "");
  const [url, setUrl] = useState(item.url ?? "");
  const [error, setError] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);

  const isFailed = item.status === "failed";
  // Unsaved edits: commit indexes the *server* row, so block committing until
  // the edit is persisted — otherwise the pre-edit content would be indexed.
  const dirty =
    title !== (item.title ?? "") || text !== (item.text ?? "") || url !== (item.url ?? "");
  // Nothing to index when there is no title/text/url/attachment content.
  const hasContent =
    title.trim().length > 0 ||
    text.trim().length > 0 ||
    url.trim().length > 0 ||
    item.attachment_count > 0;

  const save = useMutation({
    mutationFn: () =>
      patchCapture(client, item.id, {
        title: title.trim() || null,
        text: text.trim() || null,
        url: url.trim() || null,
      }),
    onSuccess: () => {
      setError(null);
      // Refresh the inbox so the card behind this panel reflects the edit.
      void qc.invalidateQueries({ queryKey: ["captures", "list"] });
    },
    onError: (e) => setError(errMsg(e)),
  });

  const commit = useMutation({
    mutationFn: () => indexCapture(client, item.id),
    onSuccess: () => {
      setError(null);
      onRemoved(item.id); // leaves the inbox (now indexed)
      onClose();
    },
    onError: (e) => setError(errMsg(e)),
  });

  const remove = useMutation({
    mutationFn: () => deleteCapture(client, item.id),
    onSuccess: () => {
      setError(null);
      onRemoved(item.id);
      onClose();
    },
    onError: (e) => setError(errMsg(e)),
  });

  const busy = save.isPending || commit.isPending || remove.isPending;

  return (
    <section aria-label="Capture detail" className="mb-4">
      <Card>
        <CardContent className="flex flex-col gap-4">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor={`cap-title-${item.id}`}>Title</Label>
            <Input id={`cap-title-${item.id}`} type="text" value={title} onChange={(e) => setTitle(e.target.value)} />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor={`cap-note-${item.id}`}>Note</Label>
            <Textarea id={`cap-note-${item.id}`} value={text} rows={4} onChange={(e) => setText(e.target.value)} />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor={`cap-link-${item.id}`}>Link</Label>
            <Input id={`cap-link-${item.id}`} type="text" value={url} onChange={(e) => setUrl(e.target.value)} />
          </div>

          {isFailed && item.last_error ? (
            <Alert variant="destructive" role="note">
              <AlertDescription>Last attempt failed: {item.last_error}</AlertDescription>
            </Alert>
          ) : null}

          {error ? (
            <Alert variant="destructive" role="alert">
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          ) : null}

          {/* One main action at a time: Save while there are unsaved edits, otherwise Commit.
              Delete is last and asks first. */}
          <div className="flex flex-wrap gap-2">
            <Button type="button" variant={dirty ? "default" : "outline"} disabled={busy} onClick={() => save.mutate()}>
              Save
            </Button>
            <Button
              type="button"
              variant={dirty ? "outline" : "default"}
              disabled={busy || !hasContent || dirty}
              title={
                dirty
                  ? "Save your changes first, then commit"
                  : hasContent
                    ? undefined
                    : "Add a note, link, title, or attachment first"
              }
              onClick={() => commit.mutate()}
            >
              {isFailed ? "Retry — add to memory" : "Commit to memory"}
            </Button>
            <Button type="button" variant="ghost" disabled={busy} onClick={onClose}>
              Close
            </Button>
            <Button type="button" variant="destructive" disabled={busy} onClick={() => setConfirmDelete(true)}>
              Delete
            </Button>
          </div>
        </CardContent>
      </Card>

      <AlertDialog open={confirmDelete} onOpenChange={setConfirmDelete}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete this capture?</AlertDialogTitle>
            <AlertDialogDescription>
              The note and any photos or audio saved with it are removed from your inbox. This can't be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              variant="destructiveSolid"
              onClick={() => {
                setConfirmDelete(false);
                remove.mutate();
              }}
            >
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </section>
  );
}
