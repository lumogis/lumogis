// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Graceful service-degradation banners (LUM-512). Encodes the
// degraded-vs-hard-fail split:
//   * Ollama down  → hard failure: chat can't generate. role="alert".
//   * Qdrant down  → degraded: vector/document search off, chat still works.
//   * Graph down   → degraded: entity/KG features off, chat still works.
// Degraded banners are role="status" (non-interrupting); the hard-fail banner
// is role="alert". Renders nothing when all relevant services are healthy.

import type { ServiceHealth } from "./useServiceHealth";
import { TriangleAlert } from "lucide-react";

import { Alert, AlertDescription } from "../../components/ui/alert";
import { help } from "../../copy/help";

export interface ServiceDegradationBannerProps {
  health: ServiceHealth;
}

export function ServiceDegradationBanner({ health }: ServiceDegradationBannerProps): JSX.Element | null {
  const { isOllamaDown, isQdrantDown, isGraphDown } = health;

  if (!isOllamaDown && !isQdrantDown && !isGraphDown) {
    return null;
  }

  // LUM-993: compact Alerts at the top of the chat; same three messages, no
  // shell command (the persistent-error line points at the Health page).
  return (
    <div className="mb-3 flex flex-col gap-2" data-testid="service-degradation">
      {isOllamaDown ? (
        <Alert variant="destructive">
          <TriangleAlert aria-hidden="true" />
          <AlertDescription>
            <span>
              <strong>Local AI unavailable.</strong> Lumogis can&rsquo;t reach Ollama, so messages may fail until
              it&rsquo;s back. {help.errors.persistent}
            </span>
          </AlertDescription>
        </Alert>
      ) : null}

      {isQdrantDown ? (
        <Alert role="status" className="border-warning/40 bg-warning/10">
          <TriangleAlert aria-hidden="true" className="text-warning" />
          <AlertDescription className="text-foreground">
            <span>
              <strong>Document search unavailable.</strong> Chat is using your knowledge graph only.
            </span>
          </AlertDescription>
        </Alert>
      ) : null}

      {isGraphDown ? (
        <Alert role="status" className="border-warning/40 bg-warning/10">
          <TriangleAlert aria-hidden="true" className="text-warning" />
          <AlertDescription className="text-foreground">
            <span>
              <strong>Knowledge graph temporarily unavailable.</strong> Basic chat still works; entity links may
              not resolve.
            </span>
          </AlertDescription>
        </Alert>
      ) : null}
    </div>
  );
}
