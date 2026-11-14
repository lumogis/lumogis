// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-695 read side: the attachments on a surfaced message. The parent owns
// fetching the metadata and passes rows + state; this component owns the
// download, because a download here is a fetch and not a link.
//
// Two states this component exists to get right:
//
//   * `available: false` — a metadata-mode mailbox never fetches bodies, so no
//     attachment data was ever extracted. Rendering "No attachments" there
//     would be an outright lie about a message that has three. It says so.
//   * 503 vs 410 on download — the mailbox being unreachable is retryable, a
//     message deleted upstream never will be. Bytes are fetched on demand, so
//     that difference is the honest cost of not storing them, and the UI names
//     it rather than showing one generic failure.

import { useCallback, useState } from "react";

import { ApiError, type ApiClient } from "../../api/client";
import { downloadAttachment, formatBytes, type AttachmentRow } from "../../api/emailAttachments";
import { acceptCandidates, explainAcceptSkip } from "../../api/emailAttachmentIngest";

export interface AttachmentListProps {
  signalId: string;
  rows: AttachmentRow[];
  /** false → metadata-mode mailbox: "cannot know", NOT "none". */
  available: boolean;
  loading?: boolean;
  /** Set when the LIST failed (not a download) — the parent's fetch error. */
  error?: string | null;
  /** Required to download: auth is a bearer token, so this cannot be a link. */
  client: ApiClient;
  /** LUM-727: offer "File to library" here, on the message itself.
   *
   *  The review list is for catching up in bulk; this is for the moment the
   *  user is already looking at the invoice and knows they want it. Off by
   *  default so the component stays usable anywhere it is already mounted —
   *  a mailbox that never opted in has no ledger to file into. */
  canFile?: boolean;
}

function downloadErrorMessage(err: unknown): string {
  if (err instanceof ApiError) {
    switch (err.status) {
      case 503:
        return "Can’t reach the mailbox right now. Try again in a moment.";
      case 410:
        // Deliberately distinct from 503: this one will never succeed.
        return "This message is no longer in the mailbox, so its attachment is gone.";
      case 413:
        return "This file is too large to open here.";
      case 415:
        return "This file type isn’t served for safety.";
      case 429:
        return "Too many downloads — try again shortly.";
      case 409:
        // Several messages claim the same Message-ID, so we cannot tell which
        // one this attachment belongs to. Refusing beats serving the wrong file.
        return "Couldn’t confirm which message this belongs to, so it wasn’t opened.";
      default:
        return "Couldn’t open that attachment.";
    }
  }
  return "Couldn’t open that attachment.";
}

export function AttachmentList({
  signalId,
  rows,
  available,
  loading,
  error,
  client,
  canFile = false,
}: AttachmentListProps): JSX.Element | null {
  const [busy, setBusy] = useState<number | null>(null);
  const [downloadError, setDownloadError] = useState<string | null>(null);
  const [filing, setFiling] = useState<number | null>(null);
  const [filed, setFiled] = useState<Set<number>>(new Set());
  const [fileStatus, setFileStatus] = useState<string | null>(null);

  const onFile = useCallback(
    async (row: AttachmentRow) => {
      setFileStatus(null);
      setFiling(row.part_index);
      try {
        const res = await acceptCandidates(client, [
          { signal_id: signalId, part_index: row.part_index },
        ]);
        if (res.accepted > 0) {
          setFiled((f) => new Set(f).add(row.part_index));
          setFileStatus(`${row.filename} is in your library`);
          return;
        }
        // Not an error — `duplicate` and `no_extractor` are ordinary outcomes,
        // and the reason is the part worth showing.
        const reason = res.skipped[0]?.reason ?? "not_found";
        setFileStatus(explainAcceptSkip(reason));
      } catch {
        setFileStatus("Couldn’t file that — try again.");
      } finally {
        setFiling(null);
      }
    },
    [client, signalId],
  );

  const onDownload = useCallback(
    async (row: AttachmentRow) => {
      setDownloadError(null);
      setBusy(row.part_index);
      try {
        await downloadAttachment(client, signalId, row.part_index, row.filename);
      } catch (err) {
        setDownloadError(downloadErrorMessage(err));
      } finally {
        setBusy(null);
      }
    },
    [client, signalId],
  );

  if (loading) {
    return (
      <p className="lumogis-attachments__status" role="status">
        Loading attachments…
      </p>
    );
  }

  if (error) {
    return (
      <p className="lumogis-attachments__status lumogis-attachments__status--error" role="alert">
        Couldn’t load attachments.
      </p>
    );
  }

  if (!available) {
    // NOT an empty list. This mailbox is in metadata mode, so we genuinely do
    // not know whether this message has attachments.
    return (
      <p className="lumogis-attachments__status" data-testid="attachments-unavailable" role="note">
        Attachments need full-ingest mode for this mailbox.
      </p>
    );
  }

  if (rows.length === 0) {
    // Now this really does mean none — we looked, and there were none.
    return null;
  }

  return (
    <div className="lumogis-attachments" aria-label="Attachments">
      <ul className="lumogis-attachments__list">
        {rows.map((row) => (
          <li key={row.part_index} className="lumogis-attachments__item">
            {/* A button, not a link: the download is an authenticated fetch.
                The server always responds Content-Disposition: attachment +
                nosniff, so nothing here can ever render in-page. */}
            <button
              type="button"
              className="lumogis-attachments__link"
              disabled={busy !== null}
              onClick={() => void onDownload(row)}
            >
              {row.filename}
            </button>
            <span className="lumogis-attachments__size">{formatBytes(row.size_bytes)}</span>
            {/* Nothing is filed automatically since LUM-727, so this is how a
                single attachment gets into the library without a trip to the
                review list. */}
            {canFile ? (
              filed.has(row.part_index) ? (
                <span className="lumogis-attachments__filed" data-testid="attachment-filed">
                  Filed
                </span>
              ) : (
                <button
                  type="button"
                  className="lumogis-attachments__file"
                  disabled={filing !== null}
                  onClick={() => void onFile(row)}
                  aria-label={`File ${row.filename} to library`}
                >
                  File to library
                </button>
              )
            ) : null}
          </li>
        ))}
      </ul>
      {fileStatus ? (
        <p className="lumogis-attachments__status" role="status" data-testid="attachment-file-status">
          {fileStatus}
        </p>
      ) : null}
      {downloadError ? (
        <p className="lumogis-attachments__status lumogis-attachments__status--error" role="alert">
          {downloadError}
        </p>
      ) : null}
      <p className="lumogis-attachments__note">
        Attachments open from your mailbox — it needs to be reachable, and a message deleted
        there takes its attachments with it.
      </p>
    </div>
  );
}
