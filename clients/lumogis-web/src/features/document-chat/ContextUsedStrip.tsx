// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis

import type { DocumentCitationDTO } from "../../api/chat";

export interface ContextUsedStripProps {
  citations: DocumentCitationDTO[];
}

function formatChunkIndices(citations: DocumentCitationDTO[]): string {
  const indices = citations
    .map((c) => c.chunk_index)
    .filter((idx): idx is number => idx !== null && idx !== undefined);
  if (indices.length === 0) return "—";
  return indices.join(", ");
}

export function ContextUsedStrip({ citations }: ContextUsedStripProps): JSX.Element | null {
  if (citations.length === 0) return null;
  return (
    <p className="text-sm text-muted" data-testid="context-used-strip">
      Context used: chunks {formatChunkIndices(citations)}
    </p>
  );
}
