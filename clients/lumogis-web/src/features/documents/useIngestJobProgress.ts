// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Poll ingest job progress (LUM-511).

import { useQuery } from "@tanstack/react-query";

import type { ApiClient } from "../../api/client";
import { getIngestJob, type IngestJobProgress } from "../../api/ingest";

export const ingestJobQueryKey = (jobId: number) => ["ingest-job", jobId] as const;

function isTerminal(progress: IngestJobProgress | undefined): boolean {
  if (!progress) return false;
  // LUM-157: ``partial`` is a terminal share outcome (some sections still
  // indexing) — stop polling, don't spin forever waiting for ``done``.
  if (
    progress.stage === "done" ||
    progress.stage === "failed" ||
    progress.stage === "partial"
  )
    return true;
  return progress.status === "done" || progress.status === "dead";
}

export function useIngestJobProgress(client: ApiClient, jobId: number | null) {
  return useQuery({
    queryKey: ingestJobQueryKey(jobId ?? 0),
    queryFn: () => getIngestJob(client, jobId!),
    enabled: jobId !== null && jobId > 0,
    refetchInterval: (q) => (isTerminal(q.state.data) ? false : 1000),
  });
}
