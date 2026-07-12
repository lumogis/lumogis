// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Capture inbox list hook (LUM-606). Plain `useQuery` (the house idiom — see
// features/documents/useDocuments.ts); "Load more" grows `limit` so there is a
// single cache entry and `removeFromList` (setQueryData drop) stays trivial.
// Parameterised by status set so LUM-607's archive reuses it with ["indexed"].

import { useQuery, useQueryClient } from "@tanstack/react-query";

import type { ApiClient } from "../../api/client";
import {
  listCaptures,
  type CaptureListResponse,
  type CaptureStatus,
} from "../../api/captures";

export const CAPTURE_PAGE_SIZE = 20;

export const captureListKey = (statuses: CaptureStatus[], limit: number) =>
  ["captures", "list", [...statuses].sort(), limit] as const;

export function useCaptureList(client: ApiClient, statuses: CaptureStatus[], limit: number) {
  const qc = useQueryClient();
  const key = captureListKey(statuses, limit);

  const query = useQuery({
    queryKey: key,
    queryFn: () => listCaptures(client, { status: statuses, limit, offset: 0 }),
  });

  // Drop a row from the cached page on commit/delete success (no refetch).
  const removeFromList = (id: string): void => {
    qc.setQueryData<CaptureListResponse>(key, (prev) =>
      prev
        ? {
            ...prev,
            captures: prev.captures.filter((c) => c.id !== id),
            total: Math.max(0, prev.total - 1),
          }
        : prev,
    );
  };

  return { ...query, removeFromList };
}
