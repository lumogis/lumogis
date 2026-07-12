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
    <section
      aria-label="Capture detail"
      style={{ border: "1px solid #bdbdbd", borderRadius: 8, padding: "1rem", marginBottom: "1rem" }}
    >
      <label style={{ display: "block", marginBottom: "0.5rem" }}>
        Title
        <input
          type="text"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          style={{ display: "block", width: "100%" }}
        />
      </label>
      <label style={{ display: "block", marginBottom: "0.5rem" }}>
        Note
        <textarea
          value={text}
          rows={4}
          onChange={(e) => setText(e.target.value)}
          style={{ display: "block", width: "100%" }}
        />
      </label>
      <label style={{ display: "block", marginBottom: "0.75rem" }}>
        Link
        <input
          type="text"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          style={{ display: "block", width: "100%" }}
        />
      </label>

      {isFailed && item.last_error ? (
        <p role="note" style={{ color: "#c62828", fontSize: "0.9rem" }}>
          Last attempt failed: {item.last_error}
        </p>
      ) : null}

      {error ? (
        <p role="alert" style={{ color: "#c62828" }}>
          {error}
        </p>
      ) : null}

      <div style={{ display: "flex", flexWrap: "wrap", gap: "0.5rem" }}>
        <button type="button" disabled={busy} onClick={() => save.mutate()}>
          Save
        </button>
        <button
          type="button"
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
        </button>
        <button type="button" disabled={busy} onClick={() => remove.mutate()}>
          Delete
        </button>
        <button type="button" disabled={busy} onClick={onClose}>
          Close
        </button>
      </div>
    </section>
  );
}
