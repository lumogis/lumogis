// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis

import type { JSX } from "react";

import styles from "./wow.module.css";

const GUIDED_PROMPTS = [
  "What deadlines or commitments appear across my documents?",
  "Which people or organisations show up in more than one place, and how are they connected?",
  "Summarise recurring themes across everything Lumogis has ingested so far.",
  "What should I follow up on based on notes and files Lumogis knows about?",
] as const;

export interface GuidedFirstQueryCardProps {
  onPickPrompt: (prompt: string) => void;
  onTypeOwn: () => void;
  onDismiss: () => void;
  isDismissing?: boolean;
}

export function GuidedFirstQueryCard({
  onPickPrompt,
  onTypeOwn,
  onDismiss,
  isDismissing = false,
}: GuidedFirstQueryCardProps): JSX.Element {
  return (
    <section
      className={styles.card}
      data-testid="wow-guided-card"
      aria-labelledby="wow-guided-title"
    >
      <h2 id="wow-guided-title" className={styles.cardTitle}>
        Try your first question
      </h2>
      <p className={styles.cardIntro}>
        Lumogis can look across everything you have ingested. Pick a suggestion or type your own.
      </p>
      <ul className={styles.promptList} role="list">
        {GUIDED_PROMPTS.map((prompt) => (
          <li key={prompt}>
            <button
              type="button"
              className={styles.promptButton}
              onClick={() => onPickPrompt(prompt)}
            >
              {prompt}
            </button>
          </li>
        ))}
      </ul>
      <div className={styles.actions}>
        <button type="button" onClick={onTypeOwn}>
          Type my own question
        </button>
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
