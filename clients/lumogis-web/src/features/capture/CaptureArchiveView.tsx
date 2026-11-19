// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Capture archive (LUM-607): read-only list of committed (indexed) captures.
// Reuses the LUM-606 seam — `useCaptureList(["indexed"])` + the status-agnostic
// `CaptureCard` — and opens a read-only `CaptureArchiveDetail` (never the
// mutate-capable inbox detail panel).

import { useState } from "react";

import { ApiError } from "../../api/client";
import { useAuth } from "../../auth/AuthProvider";
import type { CaptureStatus } from "../../api/captures";
import { EmptyState } from "../../components/page/EmptyState";
import { ErrorState } from "../../components/page/ErrorState";
import { LoadingPlaceholder, SkeletonText } from "../../components/page/LoadingState";
import { Button } from "../../components/ui/button";
import { CaptureArchiveDetail } from "./CaptureArchiveDetail";
import { CaptureCard } from "./CaptureCard";
import { CAPTURE_PAGE_SIZE, useCaptureList } from "./useCaptureList";

const ARCHIVE_STATUSES: CaptureStatus[] = ["indexed"];

export function CaptureArchiveView(): JSX.Element {
  const { client } = useAuth();
  const [limit, setLimit] = useState(CAPTURE_PAGE_SIZE);
  const [openId, setOpenId] = useState<string | null>(null);

  const q = useCaptureList(client, ARCHIVE_STATUSES, limit);

  if (q.isPending) {
    return (
      <LoadingPlaceholder label="Loading archive…">
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
        : "Archive unavailable.";
    return <ErrorState title="Could not load the archive" message={detail} onRetry={() => void q.refetch()} />;
  }

  const { captures, total } = q.data;

  return (
    <div className="max-w-2xl">
      <p className="mt-0 mb-4 text-sm text-muted-foreground">
        Captures you've committed to memory. This is a read-only receipt — committed notes can't be
        edited or deleted here (removing something from memory is a separate action).
      </p>

      {openId ? (
        <CaptureArchiveDetail client={client} captureId={openId} onClose={() => setOpenId(null)} />
      ) : null}

      {captures.length === 0 ? (
        <EmptyState
          title="Nothing committed yet"
          helperText="Notes you commit to memory from the Inbox appear here."
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
