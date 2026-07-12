// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Capture inbox (LUM-606): a light note-vault of the household member's server
// captures that are pending or failed. Notes live here until committed to
// memory (indexed), which removes them from the inbox.

import { useState } from "react";

import { ApiError } from "../../api/client";
import { useAuth } from "../../auth/AuthProvider";
import type { CaptureListItem, CaptureStatus } from "../../api/captures";
import { CaptureCard } from "./CaptureCard";
import { CaptureDetailPanel } from "./CaptureDetailPanel";
import { CAPTURE_PAGE_SIZE, useCaptureList } from "./useCaptureList";

const INBOX_STATUSES: CaptureStatus[] = ["pending", "failed"];

export function CaptureInboxView(): JSX.Element {
  const { client } = useAuth();
  const [limit, setLimit] = useState(CAPTURE_PAGE_SIZE);
  const [openId, setOpenId] = useState<string | null>(null);

  const q = useCaptureList(client, INBOX_STATUSES, limit);

  if (q.isPending) return <p>Loading inbox…</p>;
  if (q.isError) {
    const detail =
      q.error instanceof ApiError
        ? q.error.status === 401
          ? "You are signed out or your session expired."
          : q.error.detail
        : "Inbox unavailable.";
    return <p role="alert">{detail}</p>;
  }

  const { captures, total } = q.data;
  const openItem: CaptureListItem | undefined = captures.find((c) => c.id === openId);

  return (
    <div>
      <p style={{ opacity: 0.85, maxWidth: "40rem" }}>
        Notes you've saved to the household server, waiting to be committed to memory. Open one to
        edit it, delete it, or <strong>commit it to memory</strong> — committed notes leave the
        inbox and become searchable.
      </p>

      {openItem ? (
        <CaptureDetailPanel
          client={client}
          item={openItem}
          onRemoved={(id) => q.removeFromList(id)}
          onClose={() => setOpenId(null)}
        />
      ) : null}

      {captures.length === 0 ? (
        <p style={{ opacity: 0.8 }}>
          Your inbox is empty. Captured notes appear here until you commit them to memory.
        </p>
      ) : (
        <>
          {captures.map((item) => (
            <CaptureCard key={item.id} item={item} onOpen={setOpenId} />
          ))}
          {captures.length < total ? (
            <button
              type="button"
              onClick={() => setLimit((n) => n + CAPTURE_PAGE_SIZE)}
              disabled={q.isFetching}
            >
              Load more ({captures.length} of {total})
            </button>
          ) : null}
        </>
      )}
    </div>
  );
}
