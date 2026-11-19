// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis

import { Link } from "react-router-dom";
import type { JSX } from "react";

import type { WowTopEntity } from "../../api/meWow";
import { Button } from "../../components/ui/button";

import { CHIP } from "./chip";

export interface EntityDiscoveryCardProps {
  entities: WowTopEntity[];
  onAskAbout: (name: string) => void;
  onDismiss: () => void;
  isDismissing?: boolean;
}

export function EntityDiscoveryCard({
  entities,
  onAskAbout,
  onDismiss,
  isDismissing = false,
}: EntityDiscoveryCardProps): JSX.Element {
  return (
    <section data-testid="wow-discovery-card" aria-labelledby="wow-discovery-title" className="flex flex-col gap-2">
      <div className="flex items-center justify-between gap-2">
        <h2 id="wow-discovery-title" className="m-0 text-xs font-medium text-muted-foreground">
          Ask about what Lumogis found
        </h2>
        <span className="flex items-center gap-1">
          <Link to="/entities" className="text-xs text-accent-text">
            See all
          </Link>
          <Button variant="ghost" size="xs" onClick={() => void onDismiss()} disabled={isDismissing}>
            Dismiss
          </Button>
        </span>
      </div>
      <ul className="m-0 flex list-none flex-wrap gap-2 p-0" role="list">
        {entities.map((entity) => (
          <li key={entity.entity_id} className="min-w-0">
            <Button
              type="button"
              variant="outline"
              className={CHIP}
              aria-label={`Ask Lumogis about ${entity.name}`}
              title={`${entity.entity_type} · ${entity.mention_count} mentions`}
              onClick={() => onAskAbout(entity.name)}
            >
              {entity.name}
            </Button>
          </li>
        ))}
      </ul>
    </section>
  );
}
