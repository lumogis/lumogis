// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis

import { Link } from "react-router-dom";
import type { JSX } from "react";

import type { WowTopEntity } from "../../api/meWow";
import styles from "./wow.module.css";

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
    <section
      className={styles.card}
      data-testid="wow-discovery-card"
      aria-labelledby="wow-discovery-title"
    >
      <h2 id="wow-discovery-title" className={styles.cardTitle}>
        Entities Lumogis found
      </h2>
      <p className={styles.cardIntro}>
        These show up across your ingested content. Ask about one or browse all in Search.
      </p>
      <ul className={styles.entityList} role="list">
        {entities.map((entity) => (
          <li key={entity.entity_id} className={styles.entityRow}>
            <div>
              <strong>{entity.name}</strong>
              <span className={styles.entityMeta}>
                {" "}
                · {entity.entity_type} · {entity.mention_count} mentions
              </span>
            </div>
            <button type="button" onClick={() => onAskAbout(entity.name)}>
              Ask Lumogis about {entity.name}
            </button>
          </li>
        ))}
      </ul>
      <div className={styles.actions}>
        <Link to="/search" className={styles.secondaryLink}>
          View all entities
        </Link>
        <button
          type="button"
          className={styles.dismissButton}
          onClick={() => void onDismiss()}
          disabled={isDismissing}
        >
          Dismiss
        </button>
      </div>
    </section>
  );
}
