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
import styles from "./ServiceDegradationBanner.module.css";

export interface ServiceDegradationBannerProps {
  health: ServiceHealth;
}

export function ServiceDegradationBanner({ health }: ServiceDegradationBannerProps): JSX.Element | null {
  const { isOllamaDown, isQdrantDown, isGraphDown } = health;

  if (!isOllamaDown && !isQdrantDown && !isGraphDown) {
    return null;
  }

  return (
    <div className={styles.stack} data-testid="service-degradation">
      {isOllamaDown ? (
        <div className={`${styles.banner} ${styles.error}`} role="alert">
          <span className={styles.icon} aria-hidden="true">
            ⚠️
          </span>
          <span>
            <strong>Local AI unavailable.</strong> Lumogis can&rsquo;t reach Ollama, so messages
            may fail until it&rsquo;s back. Run <code>lumogis doctor</code> to check your services.
          </span>
        </div>
      ) : null}

      {isQdrantDown ? (
        <div className={`${styles.banner} ${styles.warn}`} role="status">
          <span className={styles.icon} aria-hidden="true">
            ⚠️
          </span>
          <span>
            <strong>Document search unavailable.</strong> Chat is using your knowledge graph only.
          </span>
        </div>
      ) : null}

      {isGraphDown ? (
        <div className={`${styles.banner} ${styles.warn}`} role="status">
          <span className={styles.icon} aria-hidden="true">
            ⚠️
          </span>
          <span>
            <strong>Knowledge graph temporarily unavailable.</strong> Basic chat still works;
            entity links may not resolve.
          </span>
        </div>
      ) : null}
    </div>
  );
}
