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
import { EmptyState } from "../../components/page/EmptyState";
import { ErrorState } from "../../components/page/ErrorState";
import { LoadingPlaceholder, SkeletonText } from "../../components/page/LoadingState";
import { Button } from "../../components/ui/button";
import { CaptureCard } from "./CaptureCard";
import { CaptureDetailPanel } from "./CaptureDetailPanel";
import { CAPTURE_PAGE_SIZE, useCaptureList } from "./useCaptureList";

const INBOX_STATUSES: CaptureStatus[] = ["pending", "failed"];

export function CaptureInboxView(): JSX.Element {
  const { client } = useAuth();
  const [limit, setLimit] = useState(CAPTURE_PAGE_SIZE);
  const [openId, setOpenId] = useState<string | null>(null);

  const q = useCaptureList(client, INBOX_STATUSES, limit);

  if (q.isPending) {
    return (
      <LoadingPlaceholder label="Loading inbox…">
        <SkeletonText lines={4} />
      </LoadingPlaceholder>
    );
  }
  if (q.isError) {
    const detail =
      q.error instanceof ApiError
        ? q.error.status === 401
          ? "You are signed out or your session expired."
          : q.error.detail
        : "Inbox unavailable.";
    return <ErrorState title="Could not load your inbox" message={detail} onRetry={() => void q.refetch()} />;
  }

  const { captures, total } = q.data;
  const openItem: CaptureListItem | undefined = captures.find((c) => c.id === openId);

  return (
    <div className="max-w-2xl">
      <p className="mt-0 mb-4 text-sm text-muted-foreground">
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
        <EmptyState
          title="Your inbox is empty"
          helperText="Captured notes appear here until you commit them to memory."
        />
      ) : (
        <>
          {captures.map((item) => (
            <CaptureCard key={item.id} item={item} onOpen={setOpenId} />
          ))}
          {captures.length < total ? (
            <Button
              type="button"
              variant="outline"
              onClick={() => setLimit((n) => n + CAPTURE_PAGE_SIZE)}
              disabled={q.isFetching}
            >
              Load more ({captures.length} of {total})
            </Button>
          ) : null}
        </>
      )}
    </div>
  );
}
