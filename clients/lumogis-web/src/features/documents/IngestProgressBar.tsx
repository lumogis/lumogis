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

  return (
    <div className="lumogis-ingest-progress" data-testid="ingest-progress-bar">
      <div
        className="lumogis-ingest-progress__track"
        role="progressbar"
        aria-valuenow={pct}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label={label}
      >
        <div className="lumogis-ingest-progress__fill" style={{ width: `${pct}%` }} />
      </div>
      <span className="lumogis-ingest-progress__label">{label}</span>
    </div>
  );
}
