// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-695 send side: pick files, stage them, hand the composer REFERENCES.
//
// The bytes go to disk via POST /attachments/stage and only the returned ids
// enter the action payload. That is not an optimisation — `action_proposals.payload`
// is JSONB rendered verbatim on the approval card, so base64 there would bloat
// the row, cross the wire twice, and show the approver a wall of noise instead
// of "3 files, 1.2 MB".
//
// Presentational plus its own upload lifecycle: the parent owns the staged-id
// list (it goes in the payload) and is told about every add and remove.

import { useCallback, useRef, useState } from "react";

import { ApiError } from "../../api/client";
import {
  discardStagedAttachment,
  formatBytes,
  stageAttachment,
  type StagedAttachment,
} from "../../api/emailAttachments";
import type { ApiClient } from "../../api/client";

export interface AttachmentPickerProps {
  client: ApiClient;
  /** The staged files currently on the message — owned by the parent. */
  staged: StagedAttachment[];
  onStaged: (attachment: StagedAttachment) => void;
  onRemoved: (stagedId: string) => void;
  /** Matches the backend's per-message reference cap. */
  maxFiles?: number;
  disabled?: boolean;
}

const DEFAULT_MAX_FILES = 10;

function messageForError(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 413) {
      // Two different 413s, and the difference matters to the user: one file was
      // too big, or they have too many files already waiting to send.
      return err.message.includes("staging_quota_exceeded")
        ? "You have too many files waiting to send. Send or remove some first."
        : "That file is too large to attach.";
    }
    if (err.status === 429) return "Too many uploads — try again shortly.";
    if (err.status === 507) return "Couldn’t save the file. Try again.";
  }
  return "Couldn’t attach that file.";
}

export function AttachmentPicker({
  client,
  staged,
  onStaged,
  onRemoved,
  maxFiles = DEFAULT_MAX_FILES,
  disabled,
}: AttachmentPickerProps): JSX.Element {
  const inputRef = useRef<HTMLInputElement | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const atLimit = staged.length >= maxFiles;
  const totalBytes = staged.reduce((sum, a) => sum + (a.size_bytes || 0), 0);

  const handleFiles = useCallback(
    async (files: FileList | null) => {
      if (!files || files.length === 0) return;
      setError(null);
      setBusy(true);
      try {
        let room = maxFiles - staged.length;
        for (const file of Array.from(files)) {
          if (room <= 0) {
            setError(`You can attach up to ${maxFiles} files.`);
            break;
          }
          try {
            onStaged(await stageAttachment(client, file));
            room -= 1;
          } catch (err) {
            // Report and stop: continuing past a quota or rate error would just
            // produce the same failure for every remaining file.
            setError(messageForError(err));
            break;
          }
        }
      } finally {
        setBusy(false);
        if (inputRef.current) inputRef.current.value = "";
      }
    },
    [client, maxFiles, onStaged, staged.length],
  );

  const handleRemove = useCallback(
    async (stagedId: string) => {
      // Drop it from the message first — the user asked for it gone, and a
      // failed server-side cleanup must not leave a file they removed still
      // attached to what they send. The sweep reclaims the disk either way.
      onRemoved(stagedId);
      try {
        await discardStagedAttachment(client, stagedId);
      } catch {
        // Deliberately silent: the sweep is the backstop.
      }
    },
    [client, onRemoved],
  );

  return (
    <div className="lumogis-attachment-picker" aria-label="Attachments">
      <input
        ref={inputRef}
        type="file"
        multiple
        className="lumogis-attachment-picker__input"
        data-testid="attachment-input"
        disabled={disabled || busy || atLimit}
        onChange={(e) => void handleFiles(e.target.files)}
      />

      {staged.length > 0 ? (
        <ul className="lumogis-attachment-picker__list">
          {staged.map((a) => (
            <li key={a.id} className="lumogis-attachment-picker__item">
              <span className="lumogis-attachment-picker__name">{a.filename}</span>
              <span className="lumogis-attachment-picker__size">{formatBytes(a.size_bytes)}</span>
              <button
                type="button"
                className="lumogis-attachment-picker__remove"
                aria-label={`Remove ${a.filename}`}
                disabled={disabled || busy}
                onClick={() => void handleRemove(a.id)}
              >
                Remove
              </button>
            </li>
          ))}
        </ul>
      ) : null}

      {staged.length > 0 ? (
        <p className="lumogis-attachment-picker__summary" data-testid="attachment-summary">
          {staged.length} {staged.length === 1 ? "file" : "files"}, {formatBytes(totalBytes)}
        </p>
      ) : null}

      {busy ? (
        <p className="lumogis-attachment-picker__status" role="status">
          Attaching…
        </p>
      ) : null}

      {error ? (
        <p className="lumogis-attachment-picker__status lumogis-attachment-picker__status--error" role="alert">
          {error}
        </p>
      ) : null}
    </div>
  );
}
