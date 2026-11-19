// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Accessible ingest stage progress bar (LUM-511).

import type { IngestProgressStage } from "../../api/ingest";

const STAGE_LABELS: Record<IngestProgressStage, string> = {
  queued: "Queued",
  extracting: "Extracting text",
  chunking: "Chunking",
  embedding: "Embedding",
  graph: "Updating knowledge graph",
  projecting: "Sharing sections",
  partial: "Shared (finishing)",
  done: "Done",
  failed: "Failed",
};

export interface IngestProgressBarProps {
  stage: IngestProgressStage;
  progressPct: number;
  statusMessage?: string | null;
}

export function IngestProgressBar({
  stage,
  progressPct,
  statusMessage,
}: IngestProgressBarProps): JSX.Element {
  const label = statusMessage ?? STAGE_LABELS[stage] ?? stage;
  const pct = Math.min(100, Math.max(0, progressPct));

  // A native <progress> (implicit progressbar role) so the fill needs no inline
  // width; the aria values stay explicit for the tests and older readers.
  return (
    <div className="flex flex-col gap-1" data-testid="ingest-progress-bar">
      <progress
        value={pct}
        max={100}
        aria-valuenow={pct}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label={label}
        className="h-2 w-full overflow-hidden rounded-full bg-muted [&::-moz-progress-bar]:bg-primary [&::-webkit-progress-bar]:bg-muted [&::-webkit-progress-value]:bg-primary [&::-webkit-progress-value]:transition-all"
      />
      <span className="text-xs text-muted-foreground">{label}</span>
    </div>
  );
}
